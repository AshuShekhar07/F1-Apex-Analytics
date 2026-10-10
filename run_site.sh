#!/usr/bin/env bash
# Start the Apex21 website on this computer: the API on :8000 and the website on :5173.
#
#   ./run_site.sh            then open http://localhost:5173   (Ctrl+C stops both)
#
# It always starts from this folder, uses the main folder's virtualenv and .env when this
# copy has none, replaces an API that is already running on :8000 (often an older copy),
# and if the API fails to start it shows why. The API's log goes to logs/api.log.
set -uo pipefail
cd "$(dirname "$0")"

if [ ! -f .env ] && [ -f ../F1-Apex-Analytics/.env ]; then
  cp ../F1-Apex-Analytics/.env .env && echo "Copied .env from ../F1-Apex-Analytics"
fi
[ -f .env ] || { echo "No .env here: copy .env.example to .env and set DATABASE_URL"; exit 1; }

for venv in venv ../F1-Apex-Analytics/venv; do
  if [ -f "$venv/bin/activate" ]; then
    # shellcheck disable=SC1090
    source "$venv/bin/activate"
    break
  fi
done
command -v uvicorn >/dev/null || { echo "uvicorn not found: activate the project's virtualenv first"; exit 1; }

if curl -s -o /dev/null --max-time 3 http://localhost:8000/health; then
  echo "Something is already running on port 8000: stopping the old API"
  pkill -f "uvicorn app.main:app" || true
  sleep 2
  if curl -s -o /dev/null --max-time 3 http://localhost:8000/health; then
    echo "Port 8000 is still busy and it is not an Apex21 API started with uvicorn. What is using it:"
    (command -v ss >/dev/null && ss -ltnp 'sport = :8000') || (command -v lsof >/dev/null && lsof -i :8000)
    exit 1
  fi
fi

mkdir -p logs
uvicorn app.main:app --port 8000 > logs/api.log 2>&1 &
API_PID=$!
trap 'kill $API_PID 2>/dev/null' EXIT

printf "Starting the API"
for _ in $(seq 1 60); do
  if ! kill -0 "$API_PID" 2>/dev/null; then
    echo; echo "The API stopped while starting. Its log (logs/api.log):"; tail -n 40 logs/api.log
    exit 1
  fi
  if curl -s -o /dev/null --max-time 2 http://localhost:8000/health; then break; fi
  printf "."; sleep 1
done
echo
status=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 http://localhost:8000/health)
if [ "$status" != "200" ]; then
  echo "The API is up but /health answers $status:"; curl -s --max-time 10 http://localhost:8000/health; echo
  echo "Run 'python api_check_v1.py > api_check.txt 2>&1' to find out why."
fi
echo "API running on http://localhost:8000 (docs: http://localhost:8000/docs, log: logs/api.log)"

cd frontend || exit 1
[ -d node_modules ] || npm install
npm run dev
