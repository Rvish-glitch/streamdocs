# StreamDocs

StreamDocs is a full-stack document workflow system:

- Upload one or more documents (PDFs supported)
- Process them asynchronously in the background (Celery worker)
- Stream near-real-time progress to the UI (Redis Pub/Sub → WebSocket)
- Review/edit the extracted structured result (JSON)
- Finalize and export the finalized record as JSON or CSV

## Tech Stack

- Backend: FastAPI, SQLModel, PostgreSQL, Redis, Celery
- Frontend: React + TypeScript (Vite, TanStack Router/Query, Tailwind, shadcn/ui)
- Tooling: Docker Compose for local dev + prod-like runs, Playwright for E2E tests

## Quick Start (Docker)

### Requirements

- Docker + Docker Compose

### Start the stack

```bash
docker compose watch
```

If you don't have Docker Compose "Watch" available, you can use:

```bash
docker compose up -d --build
```

### Single-image option (app only)

This repo also includes a root [Dockerfile](Dockerfile) that builds **one container** running:

- Nginx (serves the built frontend)
- FastAPI backend API
- Celery worker

You still need **Postgres + Redis** separately (e.g. via Docker Compose).

Build:

```bash
docker build -t streamdocs-all-in-one .
```

Run (example; adapt env vars to your setup):

```bash
docker run --rm -p 8080:80 \
	-e ENVIRONMENT=production \
	-e PROJECT_NAME=StreamDocs \
	-e SECRET_KEY=changeme \
	-e FIRST_SUPERUSER=admin@streamdocs.com \
	-e FIRST_SUPERUSER_PASSWORD=changeme \
	-e POSTGRES_SERVER=host.docker.internal \
	-e POSTGRES_USER=postgres \
	-e POSTGRES_PASSWORD=postgres \
	-e POSTGRES_DB=app \
	-e REDIS_URL=redis://host.docker.internal:6379/0 \
	streamdocs-all-in-one
```

The first startup can take a minute (DB init + migrations). To inspect logs:

```bash
docker compose logs -f
```

### Local URLs

- Frontend (dashboard): http://localhost:5173
- Backend (API): http://localhost:8000
- API docs (Swagger): http://localhost:8000/docs
- Adminer (DB UI): http://localhost:8080
- MailCatcher (local email inbox): http://localhost:1080
- Traefik UI (optional): http://localhost:8090

The Traefik UI is only started if you enable the `traefik` profile:

```bash
docker compose --profile traefik up -d
```

### Environment variables

Configuration is read from the top-level `.env` file. For deployments, you should override secrets via your CI/CD or server environment.

At minimum, review/change these before deploying:

- `SECRET_KEY`
- `POSTGRES_PASSWORD`
- `FIRST_SUPERUSER_PASSWORD`

## What’s Included

### Backend workflow

- `POST /api/v1/documents/upload` (multipart `files[]`) creates a `Document` + queues a `ProcessingJob`
- The worker processes documents and publishes progress updates to Redis
- The backend exposes a WebSocket that forwards those progress events to the frontend

### Live progress WebSocket

- `GET /api/v1/jobs/{job_id}/ws?token=<JWT>`

The frontend uses this to show live progress on a document detail page.

### Review, finalize, export

- `PUT /api/v1/documents/{document_id}/result` (save edits)
- `POST /api/v1/documents/{document_id}/finalize`
- `GET /api/v1/documents/{document_id}/export?format=json|csv` (finalized-only)

## Local Development (without Docker)

This repo is set up so you can run services locally while keeping the same ports as Docker.

### Backend

```bash
cd backend
uv sync
source .venv/bin/activate
fastapi dev app/main.py
```

### Frontend

```bash
cd frontend
bun install
bun run dev
```

If you want the frontend to talk to a different API, set `VITE_API_URL` (see [frontend/README.md](./frontend/README.md)).

## Tests

### Full-stack (Docker Compose)

```bash
bash ./scripts/test.sh
```

### Backend only

```bash
cd backend
bash ./scripts/test.sh
```

### Frontend E2E (Playwright)

With the backend running:

```bash
docker compose up -d --wait backend
cd frontend
bunx playwright test
```





