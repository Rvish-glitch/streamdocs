#!/usr/bin/env bash
set -e

echo "========================================="
echo " Starting StreamDocs (API + Celery)     "
echo "========================================="

# 1. Run database connection check, migrations & initial admin seeding
python app/backend_pre_start.py
alembic upgrade head
python app/initial_data.py

# 2. Start Celery Worker in the background
echo "[INFO] Launching Celery background worker..."
celery -A app.worker.celery worker -l info -c 2 &
CELERY_PID=$!

# 3. Trap exit signals to gracefully terminate Celery when container stops
cleanup() {
    echo "[INFO] Received stop signal, shutting down..."
    kill -TERM "$CELERY_PID" 2>/dev/null || true
    kill -TERM "$API_PID" 2>/dev/null || true
    wait
    exit 0
}
trap cleanup SIGINT SIGTERM

# 4. Start FastAPI Server in foreground
PORT="${PORT:-8000}"
echo "[INFO] Launching FastAPI on port $PORT..."
fastapi run --workers 2 --port "$PORT" app/main.py &
API_PID=$!

# Wait on API process
wait "$API_PID"
