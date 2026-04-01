# StreamDocs

StreamDocs is a full-stack document workflow system:

- Upload one or more documents (PDF supported)
- Process them asynchronously in the background (Celery worker)
- Stream live/near-real-time progress to the UI (Redis Pub/Sub → backend WebSocket)
- Review/edit extracted structured output
- Finalize and export the finalized record

This repo is guided by the implementation plan in `assess.txt` and built on top of the FastAPI full-stack template.

## Tech Stack

- Backend: FastAPI, SQLModel, PostgreSQL
- Async processing: Celery + Redis (broker/result backend)
- Progress streaming: Redis Pub/Sub → FastAPI WebSocket
- Frontend: React + TypeScript (Vite, TanStack Router/Query, Tailwind, shadcn/ui)

## Architecture

Services:

- `frontend`: UI
- `backend`: REST API + WebSocket progress
- `worker`: Celery worker that processes documents
- `db`: PostgreSQL
- `redis`: Redis

High-level flow:

1. Upload → backend stores file on disk and creates `Document` + `ProcessingJob`.
2. Backend enqueues Celery task.
3. Worker parses/extracts and publishes progress events to Redis Pub/Sub.
4. Backend forwards progress events to the browser over WebSocket.
5. User reviews/edits results, finalizes, exports.

## Quick Start (Docker Compose)

Requirements:

- Docker + Docker Compose

Start:

```bash
docker compose watch
```

If you don't have Compose Watch available:

```bash
docker compose up -d --build
```

### Local URLs (from compose.override.yml)

- Frontend (dashboard): http://localhost:5174
- Backend (API): http://localhost:8001
- API docs (Swagger): http://localhost:8001/docs
- Adminer (DB UI): http://localhost:8081
- MailCatcher (dev inbox): http://localhost:1081
- Traefik UI (optional): http://localhost:8090

Note: local ports are intentionally shifted (e.g. `5174`, `8001`) so this stack can run side-by-side with a default stack using `5173`/`8000`.

Enable Traefik UI locally:

```bash
docker compose --profile traefik up -d
```

## Configuration

Configuration is read from the top-level `.env` file.

Minimum variables to review for deployments:

- `SECRET_KEY`
- `POSTGRES_PASSWORD`
- `FIRST_SUPERUSER_PASSWORD`

Common variables:

- `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`
- `REDIS_URL` (defaults to `redis://redis:6379/0` in Compose)
- `UPLOAD_DIR` (defaults to `./data/uploads` inside the container)

### PDF parsing timeout (30s)

PDF parsing is constrained to 30 seconds by default:

- `PARSING_TOTAL_TIMEOUT_SECONDS=30`

Implementation detail: PDF parsing runs in a separate OS subprocess so it can be hard-killed even if `pdfplumber/pdfminer` hangs.

## API (high level)

- `POST /api/v1/documents/upload` — multipart upload (`files[]`); creates `Document` and queues a `ProcessingJob`
- `GET /api/v1/documents/` — list documents with latest job status/progress
- `GET /api/v1/documents/{document_id}` — document detail (metadata + latest job + extraction result)

Live progress:

- `GET /api/v1/jobs/{job_id}/ws?token=<JWT>`

## Local Development (without Docker)

Backend:

```bash
cd backend
uv sync
source .venv/bin/activate
fastapi dev app/main.py --port 8001
```

Frontend:

```bash
cd frontend
bun install
bun run dev -- --port 5174
```

To point the frontend at a different API, set `VITE_API_URL`.

## Tests

Full-stack:

```bash
bash ./scripts/test.sh
```

Backend only:

```bash
cd backend
bash ./scripts/test.sh
```

Frontend E2E (Playwright):

```bash
docker compose up -d --wait backend
cd frontend
bunx playwright test
```

## Single-image option (app only)

The root Dockerfile builds one container that runs:

- nginx (serves built frontend)
- FastAPI backend
- Celery worker

You still need Postgres + Redis separately.

```bash
docker build -t streamdocs-all-in-one .
```





