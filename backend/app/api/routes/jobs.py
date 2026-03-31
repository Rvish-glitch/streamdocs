import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from jwt.exceptions import InvalidTokenError
from pydantic import ValidationError
from sqlmodel import Session

import jwt

from app.api.deps import CurrentUser, SessionDep
from app.core import security
from app.core.config import settings
from app.core.db import engine
from app.core.redis import get_redis_async, progress_channel
from app.core.redis import publish_progress_sync
from app.models import Document, JobStatus, ProcessingJob, ProcessingJobPublic, TokenPayload, User
from app.worker import process_document_job

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.delete("/{job_id}", status_code=204)
def delete_job(session: SessionDep, current_user: CurrentUser, job_id: uuid.UUID) -> None:
    job = session.get(ProcessingJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    doc = session.get(Document, job.document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    if not current_user.is_superuser and doc.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not enough permissions")

    if job.status == JobStatus.PROCESSING:
        raise HTTPException(status_code=400, detail="Cannot delete a processing job")

    session.delete(job)
    session.commit()
    return


@router.post("/{job_id}/retry", response_model=ProcessingJobPublic)
def retry_job(session: SessionDep, current_user: CurrentUser, job_id: uuid.UUID) -> Any:
    job = session.get(ProcessingJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    doc = session.get(Document, job.document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    if not current_user.is_superuser and doc.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not enough permissions")

    if job.status != JobStatus.FAILED:
        raise HTTPException(status_code=400, detail="Only failed jobs can be retried")

    new_job = ProcessingJob(
        document_id=doc.id,
        status=JobStatus.QUEUED,
        progress=0,
        current_stage="queued",
        error_message=None,
        updated_at=datetime.now(timezone.utc),
    )
    session.add(new_job)
    session.commit()
    session.refresh(new_job)

    publish_progress_sync(
        new_job.id,
        {
            "document_id": str(doc.id),
            "status": JobStatus.QUEUED.value,
            "stage": "queued",
            "progress": 0,
            "ts": datetime.now(timezone.utc).isoformat(),
            "message": "Queued (retry)",
        },
    )

    process_document_job.delay(str(new_job.id))  # type: ignore[attr-defined]
    return new_job


@router.websocket("/{job_id}/ws")
async def job_progress_ws(websocket: WebSocket, job_id: str) -> None:
    try:
        job_uuid = uuid.UUID(job_id)
    except ValueError:
        await websocket.accept()
        await websocket.send_text("{}")
        await websocket.close(code=1008)
        return

    # Authenticate websocket client via JWT.
    # Browsers can't set custom headers easily for WS, so we accept `?token=...`.
    token = websocket.query_params.get("token")
    if not token:
        auth = websocket.headers.get("authorization")
        if auth and auth.lower().startswith("bearer "):
            token = auth.split(" ", 1)[1].strip()

    if not token:
        await websocket.accept()
        await websocket.close(code=1008)
        return

    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[security.ALGORITHM])
        token_data = TokenPayload(**payload)
    except (InvalidTokenError, ValidationError):
        await websocket.accept()
        await websocket.close(code=1008)
        return

    with Session(engine) as session:
        user = session.get(User, token_data.sub)
        if not user or not user.is_active:
            await websocket.accept()
            await websocket.close(code=1008)
            return

        job = session.get(ProcessingJob, job_uuid)
        if not job:
            await websocket.accept()
            await websocket.close(code=1008)
            return

        doc = session.get(Document, job.document_id)
        if not doc:
            await websocket.accept()
            await websocket.close(code=1008)
            return

        if (not user.is_superuser) and (doc.owner_id != user.id):
            await websocket.accept()
            await websocket.close(code=1008)
            return

    await websocket.accept()

    redis = get_redis_async()
    pubsub = redis.pubsub()

    try:
        # Subscribe first so we don't miss early events
        await pubsub.subscribe(progress_channel(job_uuid))

        # Emit an initial snapshot (best-effort)
        with Session(engine) as session:
            job = session.get(ProcessingJob, job_uuid)
            if job:
                await websocket.send_json(
                    {
                        "job_id": str(job.id),
                        "document_id": str(job.document_id),
                        "status": job.status.value,
                        "stage": job.current_stage,
                        "progress": job.progress,
                        "ts": datetime.now(timezone.utc).isoformat(),
                        "message": "snapshot",
                    }
                )

        while True:
            message = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=1.0
            )
            if message and message.get("type") == "message":
                data = message.get("data")
                if isinstance(data, str):
                    await websocket.send_text(data)
            await asyncio.sleep(0.1)
    except WebSocketDisconnect:
        return
    finally:
        try:
            await pubsub.unsubscribe(progress_channel(job_uuid))
        except Exception:  # noqa: BLE001
            pass
        await pubsub.close()
        await redis.close()
