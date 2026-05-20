# StatuTrack

A visual regulatory change tracker for Canadian federal law.

StatuTrack ingests the official XML source for Canadian federal Acts and
Regulations from [`justicecanada/laws-lois-xml`](https://github.com/justicecanada/laws-lois-xml),
treats its git history as the version archive, and renders an inline,
section-level diff view between any two versions — alongside a clean
reader view of the current consolidated text.

See [PROPOSAL.md](PROPOSAL.md) for the full design.

## Stack

- Python 3.12, Flask, Jinja2 (server-rendered)
- SQLite (single file, including FTS5 for full-text search)
- Tailwind via the Play CDN — no JS build step
- Passenger WSGI on a CloudLinux cPanel host

## Repository layout

```
statutrack.mikkelsen.ca/        # cPanel app root (and this repo)
  passenger_wsgi.py             # Passenger entry point
  laws_xml_to_excel.py          # legacy parser — refactor target for Phase 1
  statutrack/
    parser/                     # XML -> ParsedDocument (Phase 1)
    ingest/                     # git history walker + loader (Phase 2)
    diff/                       # section-level diff engine (Phase 2)
    web/                        # Flask app, routes, templates (Phase 3-4)
    db/                         # schema + migrations
  scripts/
    bootstrap.sh
    ingest.py
    refresh.sh
  tests/
```

`laws-lois-xml` lives at `~/data/laws-lois-xml`, outside the app root so
Passenger restarts don't churn it.

## Local development

The app is designed to run under Passenger on cPanel. For local iteration:

```bash
python3.12 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
FLASK_APP=statutrack.web flask run --debug
```

## Deployment

The app is wired up to a Passenger WSGI Python app in cPanel. Routine
updates after the initial setup:

```bash
cd ~/statutrack.mikkelsen.ca
git pull
touch tmp/restart.txt
```

Full deployment steps live in [docs/DEPLOY.md](docs/DEPLOY.md) once Phase 0 lands.
