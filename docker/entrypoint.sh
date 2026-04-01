#!/usr/bin/env bash

set -euo pipefail

cd /app/backend

# Run DB readiness + migrations + initial data (same as docker-compose "prestart" service)
if [[ "${RUN_PRESTART:-1}" == "1" ]]; then
  bash scripts/prestart.sh
fi

exec /usr/bin/supervisord -c /etc/supervisor/conf.d/supervisord.conf
