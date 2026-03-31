import io
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, File, HTTPException, Query, Response, UploadFile
from sqlmodel import Session, delete, func, select

from app.api.deps import CurrentUser, SessionDep
from app.core.config import settings
from app.core.redis import publish_progress_sync
from app.models import (
    Document,
    DocumentDetailPublic,
    DocumentListItemPublic,
    DocumentsPublic,
    DocumentsUploadResponse,
    ExtractionResult,
    ExtractionResultPublic,
    ExtractionResultReviewPublic,
    ExtractionResultUpdate,
    JobStatus,
    ProcessingJob,
    ProcessingJobPublic,
    ReviewStatus,
    get_datetime_utc,
)
from app.worker import process_document_job

router = APIRouter(prefix="/documents", tags=["documents"])


def _upload_dir() -> Path:
    upload_dir = Path(settings.UPLOAD_DIR)
    upload_dir.mkdir(parents=True, exist_ok=True)
    return upload_dir


def _latest_jobs_by_document_id(session: Session, document_ids: list[uuid.UUID]) -> dict[uuid.UUID, ProcessingJob]:
    if not document_ids:
        return {}
    jobs = session.exec(
        select(ProcessingJob)
        .where(ProcessingJob.document_id.in_(document_ids))
        .order_by(ProcessingJob.created_at.desc())
    ).all()
    latest: dict[uuid.UUID, ProcessingJob] = {}
    for job in jobs:
        if job.document_id not in latest:
            latest[job.document_id] = job
    return latest


def _results_by_document_id(session: Session, document_ids: list[uuid.UUID]) -> dict[uuid.UUID, ExtractionResult]:
    if not document_ids:
        return {}
    results = session.exec(
        select(ExtractionResult).where(ExtractionResult.document_id.in_(document_ids))
    ).all()
    return {r.document_id: r for r in results}


def _to_list_item(doc: Document, latest_job: ProcessingJob | None, result: ExtractionResult | None) -> DocumentListItemPublic:
    return DocumentListItemPublic(
        id=doc.id,
        original_filename=doc.original_filename,
        content_type=doc.content_type,
        size_bytes=doc.size_bytes,
        created_at=doc.created_at,
        latest_job=ProcessingJobPublic.model_validate(latest_job) if latest_job else None,
        result=ExtractionResultReviewPublic(review_status=result.review_status) if result else None,
    )


def _to_detail(doc: Document, latest_job: ProcessingJob | None, result: ExtractionResult | None) -> DocumentDetailPublic:
    return DocumentDetailPublic(
        id=doc.id,
        original_filename=doc.original_filename,
        content_type=doc.content_type,
        size_bytes=doc.size_bytes,
        created_at=doc.created_at,
        latest_job=ProcessingJobPublic.model_validate(latest_job) if latest_job else None,
        result=ExtractionResultPublic.model_validate(result) if result else None,
    )


@router.get("/", response_model=DocumentsPublic)
def list_documents(
    session: SessionDep,
    current_user: CurrentUser,
    q: str | None = None,
    status: JobStatus | None = None,
    sort: Literal["created_at", "filename"] = "created_at",
    order: Literal["asc", "desc"] = "desc",
    skip: int = 0,
    limit: int = Query(default=50, le=200),
) -> Any:
    statement = select(Document)
    count_statement = select(func.count()).select_from(Document)
    if not current_user.is_superuser:
        statement = statement.where(Document.owner_id == current_user.id)
        count_statement = count_statement.where(Document.owner_id == current_user.id)
    if q:
        statement = statement.where(Document.original_filename.ilike(f"%{q}%"))
        count_statement = count_statement.where(Document.original_filename.ilike(f"%{q}%"))

    if sort == "filename":
        statement = statement.order_by(
            Document.original_filename.asc() if order == "asc" else Document.original_filename.desc()
        )
    else:
        statement = statement.order_by(
            Document.created_at.asc() if order == "asc" else Document.created_at.desc()
        )

    if status is None:
        count = session.exec(count_statement).one()
        docs = session.exec(statement.offset(skip).limit(limit)).all()
        doc_ids = [d.id for d in docs]
        latest_jobs = _latest_jobs_by_document_id(session, doc_ids)
        results = _results_by_document_id(session, doc_ids)
        items = [_to_list_item(d, latest_jobs.get(d.id), results.get(d.id)) for d in docs]
        return DocumentsPublic(data=items, count=count)

    # If filtering by status, apply it against each document's latest job.
    docs_all = session.exec(statement).all()
    doc_ids = [d.id for d in docs_all]
    latest_jobs = _latest_jobs_by_document_id(session, doc_ids)
    results = _results_by_document_id(session, doc_ids)

    filtered_docs: list[Document] = []
    for d in docs_all:
        latest = latest_jobs.get(d.id)
        if latest and latest.status == status:
            filtered_docs.append(d)

    count = len(filtered_docs)
    page_docs = filtered_docs[skip : skip + limit]
    items = [_to_list_item(d, latest_jobs.get(d.id), results.get(d.id)) for d in page_docs]
    return DocumentsPublic(data=items, count=count)


@router.post("/upload", response_model=DocumentsUploadResponse)
def upload_documents(
    session: SessionDep,
    current_user: CurrentUser,
    files: list[UploadFile] = File(...),
) -> Any:
    if not files:
        raise HTTPException(status_code=400, detail="No files provided")

    upload_dir = _upload_dir()
    documents_out: list[DocumentDetailPublic] = []

    for file in files:
        doc_id = uuid.uuid4()
        original_filename = file.filename or "uploaded-file"
        suffix = Path(original_filename).suffix
        stored_name = f"{doc_id}{suffix}"
        storage_path = upload_dir / stored_name

        # Save to disk
        with storage_path.open("wb") as f:
            while True:
                chunk = file.file.read(1024 * 1024)
                if not chunk:
                    break
                f.write(chunk)

        size_bytes = storage_path.stat().st_size

        doc = Document(
            id=doc_id,
            owner_id=current_user.id,
            original_filename=original_filename,
            content_type=file.content_type,
            size_bytes=size_bytes,
            storage_path=str(storage_path),
            created_at=get_datetime_utc(),
        )
        session.add(doc)
        session.commit()
        session.refresh(doc)

        job = ProcessingJob(
            document_id=doc.id,
            status=JobStatus.QUEUED,
            progress=0,
            current_stage="queued",
            updated_at=get_datetime_utc(),
        )
        session.add(job)
        session.commit()
        session.refresh(job)

        publish_progress_sync(
            job.id,
            {
                "document_id": str(doc.id),
                "status": JobStatus.QUEUED.value,
                "stage": "queued",
                "progress": 0,
                "ts": get_datetime_utc().isoformat(),
                "message": "Queued",
            },
        )

        # Enqueue background processing
        process_document_job.delay(str(job.id))  # type: ignore[attr-defined]

        documents_out.append(_to_detail(doc, job, None))

    return DocumentsUploadResponse(documents=documents_out)


@router.get("/{document_id}", response_model=DocumentDetailPublic)
def get_document_detail(
    session: SessionDep,
    current_user: CurrentUser,
    document_id: uuid.UUID,
) -> Any:
    doc = session.get(Document, document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    if not current_user.is_superuser and doc.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not enough permissions")

    latest_job = session.exec(
        select(ProcessingJob)
        .where(ProcessingJob.document_id == doc.id)
        .order_by(ProcessingJob.created_at.desc())
    ).first()
    result = session.exec(
        select(ExtractionResult).where(ExtractionResult.document_id == doc.id)
    ).first()

    return _to_detail(doc, latest_job, result)


@router.delete("/{document_id}", status_code=204)
def delete_document(
    session: SessionDep,
    current_user: CurrentUser,
    document_id: uuid.UUID,
) -> None:
    doc = session.get(Document, document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    if not current_user.is_superuser and doc.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not enough permissions")

    processing_exists = session.exec(
        select(ProcessingJob)
        .where(ProcessingJob.document_id == doc.id)
        .where(ProcessingJob.status == JobStatus.PROCESSING)
    ).first()
    if processing_exists:
        raise HTTPException(status_code=400, detail="Cannot delete document while processing")

    session.exec(delete(ExtractionResult).where(ExtractionResult.document_id == doc.id))
    session.exec(delete(ProcessingJob).where(ProcessingJob.document_id == doc.id))

    try:
        Path(doc.storage_path).unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        pass

    session.delete(doc)
    session.commit()
    return


@router.put("/{document_id}/result", response_model=ExtractionResultPublic)
def update_result(
    session: SessionDep,
    current_user: CurrentUser,
    document_id: uuid.UUID,
    body: ExtractionResultUpdate,
) -> Any:
    doc = session.get(Document, document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    if not current_user.is_superuser and doc.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not enough permissions")

    result = session.exec(
        select(ExtractionResult).where(ExtractionResult.document_id == doc.id)
    ).first()

    if result and result.review_status == ReviewStatus.FINAL:
        raise HTTPException(status_code=400, detail="Cannot edit a finalized result")

    latest_job = session.exec(
        select(ProcessingJob)
        .where(ProcessingJob.document_id == doc.id)
        .order_by(ProcessingJob.created_at.desc())
    ).first()
    if not latest_job:
        raise HTTPException(status_code=400, detail="No processing job found")
    if latest_job.status == JobStatus.PROCESSING:
        raise HTTPException(status_code=400, detail="Cannot save edits while processing")

    # User action: Save edits -> 90%
    latest_job.current_stage = "saved"
    latest_job.progress = 90
    latest_job.updated_at = get_datetime_utc()
    session.add(latest_job)

    now = datetime.now(timezone.utc)
    if result:
        result.extracted_json = body.extracted_json
        result.review_status = ReviewStatus.DRAFT
        result.updated_at = now
        result.finalized_at = None
        result.job_id = latest_job.id
        session.add(result)
        session.commit()
        session.refresh(result)

        publish_progress_sync(
            latest_job.id,
            {
                "document_id": str(doc.id),
                "status": latest_job.status.value,
                "stage": "saved",
                "progress": 90,
                "ts": get_datetime_utc().isoformat(),
                "message": "Saved edits",
            },
        )
        return result

    new_result = ExtractionResult(
        document_id=doc.id,
        job_id=latest_job.id,
        extracted_json=body.extracted_json,
        review_status=ReviewStatus.DRAFT,
        updated_at=now,
        finalized_at=None,
    )
    session.add(new_result)
    session.commit()
    session.refresh(new_result)

    publish_progress_sync(
        latest_job.id,
        {
            "document_id": str(doc.id),
            "status": latest_job.status.value,
            "stage": "saved",
            "progress": 90,
            "ts": get_datetime_utc().isoformat(),
            "message": "Saved edits",
        },
    )
    return new_result


@router.post("/{document_id}/finalize", response_model=ExtractionResultPublic)
def finalize_result(
    session: SessionDep,
    current_user: CurrentUser,
    document_id: uuid.UUID,
) -> Any:
    doc = session.get(Document, document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    if not current_user.is_superuser and doc.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not enough permissions")

    result = session.exec(
        select(ExtractionResult).where(ExtractionResult.document_id == doc.id)
    ).first()
    if not result:
        raise HTTPException(status_code=404, detail="Extraction result not found")

    latest_job = session.exec(
        select(ProcessingJob)
        .where(ProcessingJob.document_id == doc.id)
        .order_by(ProcessingJob.created_at.desc())
    ).first()
    if not latest_job:
        raise HTTPException(status_code=400, detail="No processing job found")
    if latest_job.status == JobStatus.PROCESSING:
        raise HTTPException(status_code=400, detail="Cannot finalize while processing")

    result.review_status = ReviewStatus.FINAL
    result.finalized_at = datetime.now(timezone.utc)
    result.updated_at = datetime.now(timezone.utc)
    session.add(result)

    # User action: Finalize -> 100%
    latest_job.current_stage = "completed"
    latest_job.progress = 100
    latest_job.status = JobStatus.COMPLETED
    latest_job.updated_at = get_datetime_utc()
    if not latest_job.finished_at:
        latest_job.finished_at = get_datetime_utc()
    session.add(latest_job)

    session.commit()
    session.refresh(result)

    publish_progress_sync(
        latest_job.id,
        {
            "document_id": str(doc.id),
            "status": latest_job.status.value,
            "stage": "completed",
            "progress": 100,
            "ts": get_datetime_utc().isoformat(),
            "message": "Finalized",
        },
    )
    return result


@router.get("/{document_id}/export")
def export_result(
    session: SessionDep,
    current_user: CurrentUser,
    document_id: uuid.UUID,
    format: Literal["json", "csv"] = "json",
) -> Any:
    doc = session.get(Document, document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    if not current_user.is_superuser and doc.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not enough permissions")

    result = session.exec(
        select(ExtractionResult).where(ExtractionResult.document_id == doc.id)
    ).first()
    if not result:
        raise HTTPException(status_code=404, detail="Extraction result not found")
    if result.review_status != ReviewStatus.FINAL:
        raise HTTPException(status_code=400, detail="Result must be finalized before export")

    if format == "json":
        return result.extracted_json

    extracted = result.extracted_json if isinstance(result.extracted_json, dict) else {"value": result.extracted_json}
    fieldnames = sorted(extracted.keys())

    buf = io.StringIO()
    import csv

    writer = csv.DictWriter(buf, fieldnames=fieldnames)
    writer.writeheader()

    row: dict[str, Any] = {}
    for k in fieldnames:
        v = extracted.get(k)
        if isinstance(v, (dict, list)):
            row[k] = json.dumps(v)
        else:
            row[k] = "" if v is None else str(v)
    writer.writerow(row)

    filename = f"document-{doc.id}.csv"
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
