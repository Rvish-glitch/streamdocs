#!/usr/bin/env bash

set -euo pipefail

# Render expects the service to bind to $PORT. Locally, default to 80.
PORT="${PORT:-80}"

# Replace only our placeholder to avoid clobbering nginx variables like $host/$uri.
if grep -q "__PORT__" /etc/nginx/conf.d/default.conf; then
  sed -i "s/__PORT__/${PORT}/g" /etc/nginx/conf.d/default.conf
fi

cd /app/backend

mkdir -p "${UPLOAD_DIR:-/app/backend/data/uploads}"

# Run DB readiness + migrations + initial data (same as docker-compose "prestart" service)
# in the background so the service can bind ports quickly on platforms like Render.
if [[ "${RUN_PRESTART:-1}" == "1" ]]; then
  bash scripts/prestart.sh &
fi

exec /usr/bin/supervisord -c /etc/supervisor/conf.d/supervisord.conf
