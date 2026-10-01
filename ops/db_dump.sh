#!/usr/bin/env bash
# Copy the whole Apex21 database to one file (custom format, compressed).
#   ops/db_dump.sh                      -> apex21_YYYYMMDD_HHMM.dump from $DATABASE_URL (.env)
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] && set -a && . ./.env && set +a
: "${DATABASE_URL:?DATABASE_URL is not set}"
out="apex21_$(date +%Y%m%d_%H%M).dump"
pg_dump --format=custom --no-owner --no-privileges "$DATABASE_URL" -f "$out"
echo "wrote $out ($(du -h "$out" | cut -f1))"
