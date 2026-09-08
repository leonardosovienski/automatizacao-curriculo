#!/bin/sh
set -eu
python -m api.config
alembic upgrade head
exec uvicorn api.app:app --host 0.0.0.0 --port "${PORT:-8000}" \
  --proxy-headers --forwarded-allow-ips "${FORWARDED_ALLOW_IPS:-127.0.0.1}" \
  --no-access-log
