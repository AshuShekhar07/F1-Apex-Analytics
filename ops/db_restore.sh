#!/usr/bin/env bash
# Load a dump into another database (e.g. the hosted one). Replaces what is there.
#   ops/db_restore.sh apex21_20261001_1200.dump postgresql://USER:PASS@HOST:5432/apex21
set -euo pipefail
file="${1:?usage: ops/db_restore.sh FILE.dump TARGET_DATABASE_URL}"
target="${2:?usage: ops/db_restore.sh FILE.dump TARGET_DATABASE_URL}"
read -r -p "This REPLACES the data in the target database. Type yes to continue: " ok
[ "$ok" = "yes" ] || { echo "cancelled"; exit 1; }
pg_restore --clean --if-exists --no-owner --no-privileges --dbname "$target" "$file"
echo "restored $file"
