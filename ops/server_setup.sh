#!/usr/bin/env bash
# Set up Apex21 on a fresh Ubuntu server (AWS Lightsail or EC2, or any VPS) and start it:
#
#   curl -fsSL https://raw.githubusercontent.com/AshuShekhar07/F1-Apex-Analytics/main/ops/server_setup.sh | bash
#
# Put your database dump (ops/db_dump.sh, or any pg_dump --format=custom file ending in .dump)
# in the home folder first: it is loaded the first time, into the empty database. Safe to run
# again: it then updates the code and rebuilds (git pull + docker compose up -d --build).
#
# Steps: Docker, 2 GB of swap (small servers run out of memory building the images), the code,
# a .env with a random database password, the dump, start, and a check that the site answers.
# BRANCH (default main) and REPO can be set to deploy something else.
set -euo pipefail

BRANCH="${BRANCH:-main}"
REPO="${REPO:-https://github.com/AshuShekhar07/F1-Apex-Analytics.git}"
DIR="$HOME/F1-Apex-Analytics"
SUDO=""
[ "$(id -u)" -eq 0 ] || SUDO="sudo"
step() { printf '\n== %s\n' "$*"; }

step "1/6 Docker"
if ! command -v docker >/dev/null || ! $SUDO docker compose version >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | $SUDO sh
fi
[ -z "$SUDO" ] || $SUDO usermod -aG docker "$USER" || true
DC="$SUDO docker compose"
$DC version

step "2/6 swap"
if [ -n "$(swapon --show 2>/dev/null)" ]; then
  echo "swap already on"
else
  created=""
  if [ ! -e /swapfile ]; then
    $SUDO fallocate -l 2G /swapfile && $SUDO chmod 600 /swapfile && $SUDO mkswap /swapfile >/dev/null && created=1
  fi
  if $SUDO swapon /swapfile 2>/dev/null; then
    grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' | $SUDO tee -a /etc/fstab >/dev/null
    echo "swap on (2 GB)"
  else
    echo "could not turn on swap (continuing)"
    [ -z "$created" ] || $SUDO rm -f /swapfile
  fi
fi

step "3/6 code ($BRANCH)"
command -v git >/dev/null || { $SUDO apt-get update -q && $SUDO apt-get install -y -q git; }
if [ -d "$DIR/.git" ]; then
  git -C "$DIR" fetch -q origin "$BRANCH"
  git -C "$DIR" checkout -q "$BRANCH"
  git -C "$DIR" pull -q --ff-only origin "$BRANCH"
else
  git clone -q --branch "$BRANCH" "$REPO" "$DIR"
fi
cd "$DIR"
echo "at $(git log --oneline -1)"

step "4/6 settings (.env)"
if [ -f .env ]; then
  echo ".env already there: kept"
else
  printf 'POSTGRES_PASSWORD=%s\n' "$(openssl rand -hex 24)" > .env
  chmod 600 .env
  echo "wrote .env with a random database password"
fi
API_PORT="$(sed -n 's/^API_PORT=//p' .env)"
WEB_PORT="$(sed -n 's/^WEB_PORT=//p' .env)"

step "5/6 database"
$DC up -d db
until $DC exec -T db pg_isready -U apex21 -d apex21 >/dev/null 2>&1; do sleep 2; done
races() { $DC exec -T db psql -U apex21 -d apex21 -tAc "SELECT COUNT(*) FROM races" 2>/dev/null || echo 0; }
if [ "$(races)" = "0" ]; then
  dump="$(ls -t "$HOME"/*.dump 2>/dev/null | head -1 || true)"
  if [ -z "$dump" ]; then
    echo "The database is empty and there is no .dump file in $HOME."
    echo "Copy your dump there (see DEPLOY.md, AWS) and run this script again."
    exit 1
  fi
  echo "loading $dump"
  $DC cp "$dump" db:/tmp/apex21.dump
  $DC exec -T db pg_restore --no-owner --no-privileges -U apex21 -d apex21 /tmp/apex21.dump \
    || echo "(pg_restore reported warnings; checking the data)"
  $DC exec -T db rm -f /tmp/apex21.dump
fi
echo "races in the database: $(races)"
[ "$(races)" != "0" ] || { echo "the dump did not load any races"; exit 1; }

step "6/6 start (the first build takes several minutes)"
$DC up -d --build
printf "waiting for the API"
for _ in $(seq 1 90); do
  curl -fs -o /dev/null --max-time 3 "http://localhost:${API_PORT:-8000}/health" && break
  printf "."; sleep 2
done
echo
echo "API /health: $(curl -s --max-time 10 "http://localhost:${API_PORT:-8000}/health" || echo 'no answer: see docker compose logs api')"
echo "website: HTTP $(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "http://localhost:${WEB_PORT:-80}/" || echo '?')"
ip="$(curl -fs --max-time 5 https://checkip.amazonaws.com || true)"
echo
echo "Done. Open http://${ip:-<this server>}${WEB_PORT:+:$WEB_PORT}/"
[ -z "$SUDO" ] || echo "(log out and back in once to use docker without sudo)"
