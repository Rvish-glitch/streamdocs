import uuid
from pathlib import Path
from datetime import datetime, timezone
from typing import Any
import time
import multiprocessing as mp

import pdfplumber

from celery import Celery
from celery.exceptions import Retry, SoftTimeLimitExceeded
from sqlmodel import Session, select

from app.core.config import settings
from app.core.db import engine
from app.core.redis import publish_progress_sync
from app.models import (
    Document,
    ExtractionResult,
    JobStatus,
    ProcessingJob,
    ReviewStatus,
)

celery = Celery(
    "streamdocs",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)
celery.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
)


EVENT_PROGRESS = {
    "queued": 0,
    "job_started": 10,
    "parsing_started": 30,
    "parsing_completed": 50,
    "extraction_completed": 70,
    "saved": 90,
    "completed": 100,
    "job_failed": 100,
}


def _pdfplumber_parse_worker(storage_path: str, out_q: "mp.Queue[dict[str, Any]]") -> None:
    """Run pdfplumber parsing in an isolated process so we can hard-kill hangs."""
    try:
        text = _parse_pdf_text(storage_path)
        out_q.put({"ok": True, "text": text})
    except Exception as e:  # noqa: BLE001
        out_q.put({"ok": False, "error": f"{type(e).__name__}: {e}"})


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _publish(job: ProcessingJob, **event: Any) -> None:
    # Best-effort: publishing progress should never crash processing.
    try:
        publish_progress_sync(
            job.id,
            {
                "document_id": str(job.document_id),
                "status": job.status.value,
                "stage": job.current_stage,
                "progress": job.progress,
                "ts": _utc_now().isoformat(),
                **event,
            },
        )
    except Exception:
        return


def _is_pdf(doc: Document) -> bool:
    if doc.content_type and "pdf" in doc.content_type.lower():
        return True
    return doc.original_filename.lower().endswith(".pdf")


def _parse_pdf_text(storage_path: str) -> str:
    # pdfplumber also cannot provide meaningful byte-level progress; treat parsing as a workflow step.
    import re

    pages_out: list[str] = []
    with pdfplumber.open(storage_path) as pdf:
        for idx, page in enumerate(pdf.pages, start=1):
            # layout=True tends to preserve line breaks in a more readable way
            page_text = page.extract_text(layout=True) or page.extract_text() or ""
            page_text = page_text.replace("\r\n", "\n").replace("\r", "\n")
            page_text = re.sub(r"-\n(?=\w)", "", page_text)
            page_text = re.sub(r"[\t\f\v]+", " ", page_text)
            page_text = re.sub(r"[ ]{2,}", " ", page_text)
            page_text = re.sub(r"\n{3,}", "\n\n", page_text).strip()

            if page_text:
                pages_out.append(f"--- Page {idx} ---\n{page_text}")

    return "\n\n".join(pages_out).strip()


def _parse_pdf_text_with_progress(job: ProcessingJob, storage_path: str, session: Session) -> str:
    import re

    parsing_start = EVENT_PROGRESS["parsing_started"]
    parsing_end = EVENT_PROGRESS["parsing_completed"]

    # Hard timeout for the entire PDF parsing step.
    # We enforce this by running parsing in a child process and killing it.
    total_timeout_s = int(getattr(settings, "PARSING_TOTAL_TIMEOUT_SECONDS", 30) or 30)
    if total_timeout_s < 1:
        total_timeout_s = 30

    last_commit_at = time.monotonic()

    # Bump progress and persist it before entering parsing so the UI doesn't sit forever at exactly 30%.
    opening_progress = min(parsing_end - 1, parsing_start + 1)
    job.progress = max(int(job.progress or parsing_start), opening_progress)
    job.updated_at = _utc_now()
    session.add(job)
    session.commit()
    last_commit_at = time.monotonic()
    _publish(job, message="Opening PDF")

    job.current_stage = "parsing_started"
    session.add(job)
    session.commit()
    _publish(job, message="Parsing in progress")

    ctx = mp.get_context("spawn")
    out_q: "mp.Queue[dict[str, Any]]" = ctx.Queue(maxsize=1)
    proc = ctx.Process(target=_pdfplumber_parse_worker, args=(storage_path, out_q), daemon=True)

    start = time.monotonic()
    proc.start()

    # Heartbeat progress while waiting. We don't have per-page progress anymore,
    # but this keeps the UI from looking frozen.
    while proc.is_alive():
        elapsed = time.monotonic() - start
        if elapsed >= total_timeout_s:
            proc.terminate()
            proc.join(timeout=2.0)
            if getattr(proc, "is_alive")() and hasattr(proc, "kill"):
                proc.kill()  # type: ignore[attr-defined]
                proc.join(timeout=2.0)
            raise TimeoutError(f"PDF parsing exceeded {total_timeout_s}s")

        now = time.monotonic()
        if now - last_commit_at >= 1.0:
            # Move progress gently toward parsing_end-1 as time elapses.
            frac = min(0.99, elapsed / float(total_timeout_s))
            progress_exact = parsing_start + ((parsing_end - parsing_start - 1) * frac)
            job.progress = max(int(progress_exact), int(job.progress or parsing_start))
            job.updated_at = _utc_now()
            session.add(job)
            session.commit()
            last_commit_at = now
            _publish(job, message=f"Parsing... {int(elapsed)}s/{total_timeout_s}s")

        time.sleep(0.2)

    proc.join(timeout=0)
    try:
        result = out_q.get_nowait()
    except Exception:  # noqa: BLE001
        result = {"ok": False, "error": "No result from parser process"}

    if not result.get("ok"):
        raise RuntimeError(str(result.get("error") or "PDF parsing failed"))

    parsed_text = str(result.get("text") or "")
    parsed_text = parsed_text.replace("\r\n", "\n").replace("\r", "\n")
    parsed_text = re.sub(r"\n{3,}", "\n\n", parsed_text).strip()
    return parsed_text


def _extract_structured_fields(doc: Document, parsed_text: str | None) -> dict[str, Any]:
    # Minimal structured fields for the assignment.
    text = (parsed_text or "").strip()
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    title = lines[0][:120] if lines else doc.original_filename

    summary = ""
    if text:
        summary = (text[:400] + "…") if len(text) > 400 else text
    else:
        summary = "No embedded text could be extracted from this PDF (it may be scanned)."

    # Very small keyword extractor: count words (skip short tokens)
    import re

    words = re.findall(r"[A-Za-z]{4,}", text.lower())
    stop = {
        "this",
        "that",
        "with",
        "from",
        "have",
        "were",
        "your",
        "their",
        "they",
        "will",
        "shall",
        "would",
        "could",
        "there",
        "here",
        "when",
        "what",
        "where",
        "which",
        "about",
        "into",
        "than",
        "then",
        "them",
        "been",
        "also",
        "some",
        "such",
    }
    freq: dict[str, int] = {}
    for w in words:
        if w in stop:
            continue
        freq[w] = freq.get(w, 0) + 1
    keywords = [k for k, _ in sorted(freq.items(), key=lambda kv: kv[1], reverse=True)[:8]]

    raw_text = text[:20000] if text else ""

    return {
        "title": title,
        "category": "PDF" if _is_pdf(doc) else "Document",
        "summary": summary,
        "extracted_keywords": keywords,
        "raw_text": raw_text,
        "status": "DRAFT",
        "metadata": {
            "filename": doc.original_filename,
            "content_type": doc.content_type,
            "size_bytes": doc.size_bytes,
            "parse_note": None if text else "No embedded text extracted; OCR may be required.",
        },
    }


_SOFT_LIMIT_S = int(getattr(settings, "PARSING_TOTAL_TIMEOUT_SECONDS", 30) or 30)
if _SOFT_LIMIT_S < 1:
    _SOFT_LIMIT_S = 30
_HARD_LIMIT_S = int(getattr(settings, "PARSING_TOTAL_HARD_TIMEOUT_SECONDS", _SOFT_LIMIT_S + 5) or (_SOFT_LIMIT_S + 5))
if _HARD_LIMIT_S <= _SOFT_LIMIT_S:
    _HARD_LIMIT_S = _SOFT_LIMIT_S + 5


@celery.task(
    name="process_document_job",
    bind=True,
    soft_time_limit=_SOFT_LIMIT_S,
    time_limit=_HARD_LIMIT_S,
)
def process_document_job(self: Any, job_id: str) -> None:
    job_uuid = uuid.UUID(job_id)
    with Session(engine) as session:
        job = session.get(ProcessingJob, job_uuid)
        if not job:
            return
        doc = session.get(Document, job.document_id)
        if not doc:
            job.status = JobStatus.FAILED
            job.error_message = "Document not found"
            job.progress = 100
            job.updated_at = _utc_now()
            job.finished_at = _utc_now()
            session.add(job)
            session.commit()
            _publish(job, message=job.error_message)
            return

        # Keep QUEUED=0% visible briefly (assignment UX), then emit job_started=10%.
        time.sleep(2.0)

        job.status = JobStatus.PROCESSING
        job.started_at = _utc_now()
        job.current_stage = "job_started"
        job.progress = max(int(job.progress or 0), EVENT_PROGRESS["job_started"])
        job.error_message = None
        job.updated_at = _utc_now()
        session.add(job)
        session.commit()
        _publish(job, message="Job started")

        try:
            # Stage 1: parsing started
            job.current_stage = "parsing_started"
            job.progress = max(int(job.progress or 0), EVENT_PROGRESS["parsing_started"])
            job.updated_at = _utc_now()
            session.add(job)
            session.commit()
            _publish(job, message="Parsing started")

            parsed_text: str | None = None
            if _is_pdf(doc):
                parsed_text = _parse_pdf_text_with_progress(job, doc.storage_path, session)
            else:
                # Non-PDF is out of scope here; keep placeholder text.
                parsed_text = None

            # Stage 1b: parsing completed (placeholder)
            job.current_stage = "parsing_completed"
            job.progress = max(int(job.progress or 0), EVENT_PROGRESS["parsing_completed"])
            job.updated_at = _utc_now()
            session.add(job)
            session.commit()
            _publish(job, message="Parsing completed")

            extracted = _extract_structured_fields(doc, parsed_text)

            # Stage 2b: extraction completed
            job.current_stage = "extraction_completed"
            job.progress = max(int(job.progress or 0), EVENT_PROGRESS["extraction_completed"])
            job.updated_at = _utc_now()
            session.add(job)
            session.commit()
            _publish(job, message="Extraction completed")

            # Write/update result
            existing = session.exec(
                select(ExtractionResult).where(ExtractionResult.document_id == doc.id)
            ).first()
            if existing:
                existing.extracted_json = extracted
                existing.review_status = ReviewStatus.DRAFT
                existing.updated_at = _utc_now()
                existing.finalized_at = None
                existing.job_id = job.id
                session.add(existing)
            else:
                session.add(
                    ExtractionResult(
                        document_id=doc.id,
                        job_id=job.id,
                        extracted_json=extracted,
                        review_status=ReviewStatus.DRAFT,
                        updated_at=_utc_now(),
                    )
                )
            session.commit()

            # Stop at 70% and wait for user actions:
            # - Save edits -> 90%
            # - Finalize   -> 100%
            job.status = JobStatus.COMPLETED
            job.updated_at = _utc_now()
            job.finished_at = _utc_now()
            session.add(job)
            session.commit()
            _publish(job, message="Processing complete; awaiting review")
        except Retry:
            # Let Celery handle retries without marking the job as failed.
            raise
        except (SoftTimeLimitExceeded, TimeoutError) as e:
            countdown_s = int(getattr(settings, "PARSING_TASK_RETRY_COUNTDOWN_SECONDS", 5) or 5)
            max_retries = int(getattr(settings, "PARSING_TASK_MAX_RETRIES", 3) or 3)
            if countdown_s < 0:
                countdown_s = 5
            if max_retries < 0:
                max_retries = 0

            # If our subprocess-based parsing timed out, treat it as a hard failure
            # (the PDF consistently exceeds the allowed time).
            if isinstance(e, TimeoutError):
                job.status = JobStatus.FAILED
                job.current_stage = "job_failed"
                job.error_message = f"Timed out after {int(_SOFT_LIMIT_S)}s"
                job.progress = 100
                job.updated_at = _utc_now()
                job.finished_at = _utc_now()
                session.add(job)
                session.commit()
                _publish(job, message=job.error_message)
                raise

            attempt = int(getattr(getattr(self, "request", None), "retries", 0) or 0) + 1
            if max_retries > 0 and attempt <= max_retries:
                # Put it back into a retryable state.
                job.status = JobStatus.QUEUED
                job.current_stage = "queued"
                job.progress = 0
                job.error_message = (
                    f"Timed out after {int(_SOFT_LIMIT_S)}s; retrying ({attempt}/{max_retries})"
                )
                job.updated_at = _utc_now()
                job.finished_at = None
                session.add(job)
                session.commit()
                _publish(job, message=job.error_message)
                raise self.retry(
                    countdown=countdown_s,
                    max_retries=max_retries,
                    exc=e,
                )

            job.status = JobStatus.FAILED
            job.current_stage = "job_failed"
            job.error_message = f"Timed out after {int(_SOFT_LIMIT_S)}s"
            job.progress = 100
            job.updated_at = _utc_now()
            job.finished_at = _utc_now()
            session.add(job)
            session.commit()
            _publish(job, message=job.error_message)
            raise
        except Exception as e:  # noqa: BLE001
            job.status = JobStatus.FAILED
            job.current_stage = "job_failed"
            job.error_message = str(e)
            job.progress = 100
            job.updated_at = _utc_now()
            job.finished_at = _utc_now()
            session.add(job)
            session.commit()
            _publish(job, message=job.error_message or "Job failed")
            raise
