#!/usr/bin/env bash
# bootstrap.sh — one-time setup for a fresh cPanel deploy.
#
# Assumes you have already created the Python App in cPanel:
#   - App root:  ~/statutrack.mikkelsen.ca
#   - Subdomain: statutrack.<your-domain>
#   - Python:    3.12 (or latest available)
#
# This script:
#   1. Clones laws-lois-xml into ~/data/ if missing.
#   2. Installs Python deps into the cPanel-managed virtualenv.
#   3. Creates the SQLite database from db/schema.sql.
#   4. Touches tmp/restart.txt so Passenger reloads.
#
# Run interactively over SSH the first time.

set -euo pipefail

APP_ROOT="${APP_ROOT:-$HOME/statutrack.mikkelsen.ca}"
DATA_DIR="${DATA_DIR:-$HOME/data}"
LAWS_XML_REPO="https://github.com/justicecanada/laws-lois-xml.git"
LAWS_XML_DIR="${DATA_DIR}/laws-lois-xml"
DB_PATH="${STATUTRACK_DB:-$DATA_DIR/statutrack/statutrack.sqlite}"

VENV="${VIRTUAL_ENV:-$HOME/virtualenv/statutrack.mikkelsen.ca/3.12}"
if [[ ! -d "$VENV" ]]; then
    # cPanel "Setup Python App" creates the virtualenv at
    # ~/virtualenv/<app-root>/<py-version>. Allow override via env.
    echo "warning: expected virtualenv at $VENV not found; using VIRTUAL_ENV=$VIRTUAL_ENV" >&2
fi
PIP="$VENV/bin/pip"
PY="$VENV/bin/python"

echo "=> Installing Python dependencies"
"$PIP" install --upgrade pip
"$PIP" install -r "$APP_ROOT/requirements.txt"

mkdir -p "$DATA_DIR"
if [[ ! -d "$LAWS_XML_DIR/.git" ]]; then
    # ``--no-checkout`` keeps the working tree empty. We only ever
    # read content through ``git cat-file blob`` (see
    # statutrack.ingest.walker.read_blob), so writing out 15k files
    # to the working tree is wasted I/O — and worse, on CloudLinux
    # hosts the checkout's many helper forks can trip the per-user
    # process limit and abort partway through.
    echo "=> Cloning laws-lois-xml (objects only, no working tree)"
    git clone --no-checkout "$LAWS_XML_REPO" "$LAWS_XML_DIR"
else
    echo "=> laws-lois-xml already present, fetching latest"
    # Advance main without touching the working tree — same reasoning
    # as the clone above. ``+refs/heads/main:refs/heads/main`` updates
    # the local branch ref to whatever the remote currently points at,
    # bypassing checkout entirely.
    git -C "$LAWS_XML_DIR" fetch origin "+refs/heads/main:refs/heads/main"
fi

if [[ ! -f "$DB_PATH" ]]; then
    echo "=> Creating SQLite database at $DB_PATH"
    mkdir -p "$(dirname "$DB_PATH")"
    "$PY" -c "import sqlite3; sqlite3.connect('$DB_PATH').executescript(open('$APP_ROOT/statutrack/db/schema.sql').read())"
fi

mkdir -p "$APP_ROOT/tmp"
touch "$APP_ROOT/tmp/restart.txt"
echo "=> Bootstrap complete. Visit your subdomain to confirm the app responds."
