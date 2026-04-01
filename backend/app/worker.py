import uuid
from pathlib import Path
from datetime import datetime, timezone
from typing import Any
import time

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
    import signal
    from contextlib import contextmanager

    parsing_start = EVENT_PROGRESS["parsing_started"]
    parsing_end = EVENT_PROGRESS["parsing_completed"]

    # Optional artificial slowdown (demo/testing)
    size_bytes = 0
    try:
        size_bytes = Path(storage_path).stat().st_size
    except Exception:  # noqa: BLE001
        size_bytes = 0

    seconds_per_100kb = float(getattr(settings, "PARSING_SECONDS_PER_100KB", 0.0) or 0.0)
    min_seconds = float(getattr(settings, "PARSING_MIN_SECONDS", 0.0) or 0.0)
    target_seconds = 0.0
    if seconds_per_100kb > 0:
        target_seconds = max(min_seconds, seconds_per_100kb * (size_bytes / 100_000.0))

    publish_every_n_lines = int(
        getattr(settings, "PARSING_PROGRESS_PUBLISH_EVERY_N_LINES", 1) or 1
    )
    if publish_every_n_lines < 1:
        publish_every_n_lines = 1

    last_commit_at = time.monotonic()

    # Some PDFs can cause pdfplumber/pdfminer to hang (especially on specific pages).
    # Guard with timeouts so a single bad document doesn't stick at ~38% forever.
    open_timeout_s = int(getattr(settings, "PARSING_OPEN_TIMEOUT_SECONDS", 20) or 20)
    page_timeout_s = int(getattr(settings, "PARSING_PAGE_TIMEOUT_SECONDS", 20) or 20)
    if open_timeout_s < 1:
        open_timeout_s = 20
    if page_timeout_s < 1:
        page_timeout_s = 20

    class _Timeout(Exception):
        pass

    @contextmanager
    def _time_limit(seconds: int):
        def _handler(signum, frame):  # noqa: ARG001
            raise _Timeout()

        previous = signal.signal(signal.SIGALRM, _handler)
        signal.setitimer(signal.ITIMER_REAL, float(seconds))
        try:
            yield
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous)

    # If pdfplumber.open() is slow/hangs on some PDFs, bump progress and persist it
    # before entering pdfplumber so the UI doesn't sit forever at exactly 30%.
    opening_progress = min(parsing_end - 1, parsing_start + 1)
    job.progress = max(int(job.progress or parsing_start), opening_progress)
    job.updated_at = _utc_now()
    session.add(job)
    session.commit()
    last_commit_at = time.monotonic()
    _publish(job, message="Opening PDF")

    # First pass: extract per-page text and count lines.
    # This can take time on some PDFs, so emit coarse progress during scanning
    # to avoid looking stuck at exactly parsing_started.
    page_lines: list[list[str]] = []
    try:
        with _time_limit(open_timeout_s):
            pdf_ctx = pdfplumber.open(storage_path)
    except _Timeout:
        _publish(job, message=f"Timed out opening PDF after {open_timeout_s}s")
        return ""

    with pdf_ctx as pdf:
        total_pages = len(pdf.pages)
        for idx, page in enumerate(pdf.pages, start=1):
            # Persist an update *before* extraction of each page, because
            # page.extract_text() can take a long time on some PDFs.
            if total_pages > 0:
                frac_pages = (idx - 1) / total_pages
                progress_exact = parsing_start + ((parsing_end - parsing_start) * 0.5 * frac_pages)
                job.progress = max(
                    int(progress_exact),
                    int(job.progress or parsing_start),
                    parsing_start,
                )
            job.updated_at = _utc_now()
            session.add(job)
            session.commit()
            last_commit_at = time.monotonic()
            if total_pages > 0:
                _publish(job, message=f"Extracting PDF page {idx}/{total_pages}")

            try:
                with _time_limit(page_timeout_s):
                    page_text = page.extract_text(layout=True) or ""
                if not page_text:
                    with _time_limit(page_timeout_s):
                        page_text = page.extract_text() or ""
            except _Timeout:
                _publish(
                    job,
                    message=(
                        f"Timed out extracting page {idx}/{total_pages} after {page_timeout_s}s; skipping"
                    ),
                )
                page_text = ""
            except Exception as e:  # noqa: BLE001
                _publish(
                    job,
                    message=(
                        f"Error extracting page {idx}/{total_pages}: {e!s}; skipping"
                    ),
                )
                page_text = ""

            page_text = page_text.replace("\r\n", "\n").replace("\r", "\n")
            page_text = re.sub(r"-\n(?=\w)", "", page_text)
            page_text = re.sub(r"[\t\f\v]+", " ", page_text)
            page_text = re.sub(r"[ ]{2,}", " ", page_text)
            page_text = re.sub(r"\n{3,}", "\n\n", page_text)
            lines = [ln.strip() for ln in page_text.splitlines() if ln.strip()]
            page_lines.append(lines)

            # Coarse progress: 30% -> up to just under 50% while scanning pages.
            if total_pages > 0:
                frac_pages = idx / total_pages
                progress_exact = parsing_start + ((parsing_end - parsing_start) * 0.5 * frac_pages)
                job.progress = max(int(progress_exact), int(job.progress or parsing_start), parsing_start)
                job.updated_at = _utc_now()
                # Commit at most ~1/s (reuse existing cadence)
                now = time.monotonic()
                if now - last_commit_at >= 1.0:
                    session.add(job)
                    session.commit()
                    last_commit_at = now
                    _publish(job, message=f"Scanning PDF pages {idx}/{total_pages}")

    total_lines = sum(len(lines) for lines in page_lines)
    if total_lines <= 0:
        return ""

    # Second pass: build readable output and emit incremental progress.
    out_pages: list[str] = []
    done = 0
    last_sent_progress = int(job.progress or parsing_start)
    last_commit_at = time.monotonic()
    start_at = time.monotonic()

    job.current_stage = "parsing_started"
    session.add(job)
    session.commit()
    _publish(job, message="Parsing in progress")

    for page_idx, lines in enumerate(page_lines, start=1):
        if not lines:
            continue
        out_pages.append(f"--- Page {page_idx} ---")
        for line in lines:
            out_pages.append(line)
            done += 1

            frac = done / total_lines
            progress_exact = parsing_start + ((parsing_end - parsing_start) * frac)
            progress_exact = min(progress_exact, (parsing_end - 0.1))
            progress = int(progress_exact)
            progress = min(max(progress, parsing_start), parsing_end - 1)

            now = time.monotonic()

            # Artificial slowdown: keep pace so total parsing duration ~= target_seconds
            if target_seconds > 0:
                expected_elapsed = target_seconds * frac
                elapsed = now - start_at
                if elapsed < expected_elapsed:
                    # Sleep a bit; keep small sleeps for responsiveness.
                    time.sleep(min(expected_elapsed - elapsed, 0.05))

            # Publish progress updates (optionally every line)
            if (done % publish_every_n_lines) == 0 or done == total_lines:
                job.progress = max(progress, int(job.progress or parsing_start))
                job.updated_at = _utc_now()
                _publish(
                    job,
                    progress=round(progress_exact, 1),
                    message=f"Parsed {done}/{total_lines} lines",
                )

            if progress > last_sent_progress:
                last_sent_progress = progress

            # Commit progress to DB at most ~1/s to keep dashboard snapshots fresh.
            if now - last_commit_at >= 1.0:
                session.add(job)
                session.commit()
                last_commit_at = now

    # Final commit at end of parsing stage.
    session.add(job)
    session.commit()
    return "\n".join(out_pages).strip()


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
