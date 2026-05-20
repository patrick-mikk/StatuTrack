#!/usr/bin/env bash
# refresh.sh — incremental update, intended for nightly cron.
#
# Pulls laws-lois-xml, re-ingests only the files that changed in the
# pulled commits, then touches tmp/restart.txt so Passenger reloads.
#
# Example crontab entry (run nightly at 02:30 local):
#   30 2 * * * /home/USER/statutrack.mikkelsen.ca/scripts/refresh.sh >> /home/USER/logs/statutrack-refresh.log 2>&1

set -euo pipefail

APP_ROOT="${APP_ROOT:-$HOME/statutrack.mikkelsen.ca}"
LAWS_XML_DIR="${LAWS_XML_DIR:-$HOME/data/laws-lois-xml}"
VENV="${VIRTUAL_ENV:-$HOME/virtualenv/statutrack.mikkelsen.ca/3.12}"
PY="$VENV/bin/python"

echo "[$(date -Iseconds)] refresh starting"

git -C "$LAWS_XML_DIR" pull --ff-only

# Phase 2 will provide scripts/ingest.py. Until then this is a no-op.
if [[ -f "$APP_ROOT/scripts/ingest.py" ]]; then
    "$PY" "$APP_ROOT/scripts/ingest.py" --incremental
fi

mkdir -p "$APP_ROOT/tmp"
touch "$APP_ROOT/tmp/restart.txt"
echo "[$(date -Iseconds)] refresh complete"
