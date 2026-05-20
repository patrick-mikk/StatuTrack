# StatuTrack — Implementation Plan

Living plan for building StatuTrack on top of the existing Justice
Laws XML parser, deployed on a CloudLinux/cPanel shared host via
Passenger WSGI. Companion to [PROPOSAL.md](PROPOSAL.md) (the
why/what) — this file is the how/where, kept current as the work
lands.

## Status snapshot

| Phase | Scope | State |
|---|---|---|
| 0 | cPanel Setup Python App, subdomain, virtualenv, Passenger -> Flask hello-world, defensive `.htaccess` | **Done** |
| Redesign | Wikipedia-esque white surface, serif body, Word track-changes redlines (dark red strike + #66FFCC underline) | **Done** |
| 1 | Refactor `laws_xml_to_excel.py` -> `statutrack.parser` library with `parse_document(source) -> ParsedDocument` | **Done** |
| 2a | `statutrack.ingest.walker` — enumerate every commit that touched an XML file in `laws-lois-xml`, read blobs via `git cat-file` | **Done** |
| 2b | `statutrack.ingest.loader` — write `instruments` / `versions` / `sections` rows; blob-SHA dedup; idempotent | **Done** |
| 2c | `statutrack.diff.engine` — align sections by fid -> citation -> content similarity, emit inline `<ins>`/`<del>` HTML | **Done** |
| 2d | `scripts/ingest.py` CLI + persist precomputed diffs to `diffs` table | **In progress** |
| 3 | Flask reader view (`/instruments/<slug>`) — clean consolidated text, ToC, section deep-links | **In progress** |
| 4 | Flask diff view (`/instruments/<slug>/compare?from=...&to=...`) — inline track-changes rendering | Pending |
| 5 | FTS5 search, bilingual toggle, cron entries, deploy docs | Pending |

**Live:**
- App: https://statutrack.mikkelsen.ca/ (Phase 0 landing page)
- Health: https://statutrack.mikkelsen.ca/healthz -> `{"status":"ok"}`
- Repo: https://github.com/patrick-mikk/StatuTrack
- HEAD: `d52f096` (`Phase 2c: section-level diff engine`)

**Real-world ingest baseline:** PCMLTFR (SOR-2002-184), 18 versions
2021-02 → 2025-11, 23,222 section rows, ~28 MB SQLite. One blob-SHA
dedup hit on the two first-day commits.

## Stack

- Python 3.12 inside `~/virtualenv/statutrack.mikkelsen.ca/3.12/`
- Flask 3.1, Jinja2 (server-rendered)
- SQLite (single file, FTS5 for search)
- Tailwind via Play CDN — no JS build step
- Passenger WSGI on CloudLinux/cPanel; LiteSpeed under Apache 2.4
- `lxml` for XML parsing; `openpyxl` retained for the legacy Excel exporter

## Repository layout

```
statutrack.mikkelsen.ca/        # cPanel docroot + StatuTrack repo
  .htaccess                     # Passenger config + defensive deny rules
  passenger_wsgi.py             # imports statutrack.web.create_app
  laws_xml_to_excel.py          # CLI shim → statutrack.parser.legacy.main
  PROPOSAL.md                   # why/what
  PLAN.md                       # how/where (this file)
  README.md
  requirements.txt
  pyproject.toml
  statutrack/
    parser/
      __init__.py               # public API: parse_document(...)
      legacy.py                 # the 2000-line XML -> ParsedDocument core
    ingest/
      walker.py                 # git history → FileVersion[]
      loader.py                 # FileVersion → SQLite (versions, sections, diffs)
    diff/
      engine.py                 # SectionSnapshot pairs → Diff with inline HTML
    db/
      schema.sql                # instruments, versions, sections, diffs, sections_fts
    web/
      __init__.py               # Flask app factory
      routes.py
      templates/
        base.html               # design tokens live here (inline tailwind.config)
        index.html
  scripts/
    bootstrap.sh                # one-time deploy
    refresh.sh                  # nightly cron
    ingest.py                   # CLI for one-shot or --all ingestion
  tests/
    fixtures/
      sample_regulation.xml     # synthetic LIMS fixture
      B-1.01.xml                # real Bank Act XML, ~3.3 MB
    test_smoke.py               # Flask wiring
    test_parser.py              # parser + Bank Act regression
    test_walker.py              # synthetic git repo
    test_loader.py              # synthetic git + SQLite round-trip
    test_diff.py                # alignment + inline HTML rendering
```

Data lives **outside** the docroot:

```
~/data/laws-lois-xml/           # justicecanada/laws-lois-xml clone
~/data/statutrack/
  statutrack.sqlite             # default DATABASE_PATH
```

## Architecture decisions worth recording

1. **The app root is the docroot.** This matches the convention of the
   existing `timetable.mikkelsen.ca` app on this host and lets
   Passenger intercept every request. The risk window is that any
   period without Passenger config — for example, before the cPanel
   Python App was created — exposes source files. Mitigated by a
   defensive `.htaccess` that denies `.py` / `.md` / `.sqlite` /
   `.htaccess` / `.log` files and internal directories explicitly,
   so even a Passenger outage doesn't leak source.

2. **SQLite default path lives outside the docroot** at
   `~/data/statutrack/statutrack.sqlite`. The `.htaccess` would also
   block `.sqlite` from being served, but keeping the file out of the
   docroot is defence-in-depth and means an accidental rule change
   can't expose the database.

3. **Track-changes styling is Microsoft Word, not GitHub.**
   Deletions: dark red ink with hairline strikethrough. Insertions:
   regular ink with a 3px `#66FFCC` underline. The mint colour was
   chosen by the user; carrying it on the underline (rather than the
   glyphs) keeps body type readable on white while still using both
   palette colours.

4. **Parser stays one file for now.** `statutrack/parser/legacy.py`
   is the original 2086-line `laws_xml_to_excel.py` moved into the
   package, with `parse_document()` added near the top as the
   library-friendly entry point. The internal model/parser/renderer
   split was deliberately deferred — there is no second consumer of
   `ParsedDocument` that needs the file split yet, and the existing
   structure is already cleanly factored at the function level.

5. **Git history is read via `git cat-file blob`.** The walker never
   depends on the working tree being checked out, so a partially
   checked-out clone (we hit a CloudLinux LVE process-limit during
   `git clone` once) still works for ingest. Follow-up: switch
   `scripts/bootstrap.sh` to `git clone --bare` so we never need a
   working tree at all.

6. **`git log --follow --reverse` is buggy** — drops history through
   renames. The walker walks newest-first and reverses in Python.

7. **Diffs table only stores changed rows.** `unchanged` diffs are
   inferred at query time (section present in both versions, no diff
   row). For PCMLTFR's 18 versions that's the difference between
   ~23k diff rows and ~400k.

8. **Blob-SHA dedup is at the loader, not the walker.** The walker
   reports every commit that touched a file; the loader collapses
   consecutive entries sharing a `blob_sha`. This is the right
   layer because the dedup decision depends on what's already in the
   database (previous version's blob_sha), not on raw git state.

## Phase 2d — current focus

Goals to close the phase:

- [ ] `persist_diffs_for_instrument(conn, instrument_id)` now runs at
      the tail of `load_instrument_history`. Re-running an ingest
      against a fresh DB writes the diff rows once; re-running
      against a populated DB is a no-op via `INSERT OR IGNORE` on the
      `UNIQUE(from_version_id, to_version_id, citation)` constraint.
- [ ] `scripts/ingest.py` CLI shipping with three modes: `--slug`,
      `--path`, `--all`. Uses `git ls-tree HEAD` to enumerate files
      so it works on a half-checked-out clone.
- [ ] Full smoke against the real `laws-lois-xml` clone:
        * `python scripts/ingest.py --slug SOR-2002-184` (PCMLTFR)
        * `python scripts/ingest.py --slug B-1.01` (Bank Act)
- [ ] Two new loader tests covering the diff-persistence path
      (modified rows present, idempotent re-run).
- [ ] Resolve the test BlockingIOError seen during a pytest run while
      a background ingest was holding the WAL lock — likely fixed by
      isolating test DB connections per worker (`tmp_path`-scoped
      already), but worth confirming after the in-progress ingest
      run finishes.

## Phase 3 — Flask reader view

Done so far:

- `statutrack.web.queries` — read-only data access (uses
  `file:…?mode=ro` URI so a misbehaving route can't ever write).
  Dataclasses `InstrumentRow`, `VersionRow`, `SectionRow`.
- `routes.py` — browse landing (`/`), reader (`/instruments/<slug>`),
  version timeline (`/instruments/<slug>/versions`). `/compare`
  abort(404)s until Phase 4 lands.
- `templates/index.html` — landing now lists ingested instruments
  with version count and last-amended date.
- `templates/instrument.html` — reader: header card, version-meta
  strip, sections grouped under heading-path rules, section-anchor
  deep links (`#s-<id>`).

Remaining for Phase 3:

- `templates/versions.html` — currently missing; hitting
  `/instruments/<slug>/versions` will 500. Build a small table view:
  commit date, last-amended date, section count, compare-to-previous
  link (which routes to the not-yet-built Phase 4 compare endpoint).
- Section deep-link route `/instruments/<slug>/section/<citation>`
  for share links — currently the only addressing is via the
  in-page anchor.
- Route tests using a tiny ingested fixture DB (synthetic XML, one
  or two versions) so the templates render without hitting the
  real `~/data/statutrack/statutrack.sqlite`.

## Phase 4 — Flask diff view

Routes:

- `GET /instruments/<slug>/compare?from=<vid>&to=<vid>`
- `GET /instruments/<slug>/compare/latest` — convenience wrapper
  that compares the latest version to the immediately-preceding one.

Implementation notes:

- The view never recomputes diffs. It queries `diffs` where
  `(from_version_id, to_version_id) = (?, ?)`. `inline_html` is
  rendered with `|safe` because the persister escapes inputs before
  emitting `<ins>` / `<del>`.
- Section-level adds and removes get a small banner above the section
  body rather than wrapping the whole content in `<ins>` or `<del>`.
- A version-picker control at the top of the view lets the reader
  jump to any (from, to) pair. Default: previous → current.

## Phase 5 — Search, bilingual, cron, deploy docs

- FTS5 search: `?q=` route returning `(instrument, section)` hits with
  snippet highlighting. The schema already has `sections_fts` and the
  triggers; just needs a `/search` route and template.
- French support: `language=fra` is a first-class parameter in
  `instruments`. Add a `/fra/...` route prefix or a `?lang=fra` query
  toggle on the reader/diff/search views.
- Cron entry (nightly): `30 2 * * * ~/statutrack.mikkelsen.ca/scripts/refresh.sh ...`
- `docs/DEPLOY.md` (referenced by the README) — write once Phase 5
  lands so it captures the actual cron + permission setup that ends
  up shipped.

## Follow-ups (small, deferred)

- ✅ `scripts/bootstrap.sh` now clones with `--no-checkout` and
  `scripts/refresh.sh` uses `git fetch +refs/heads/main:...` —
  ingest reads through `git cat-file blob` so a working tree is
  pure waste of I/O and process count on this LVE-capped host.
- Replace the Tailwind Play CDN with a built CSS file once Phase 5
  ships, IF the runtime warning becomes noisy or the unused-class
  payload starts to matter. The proposal explicitly accepts the CDN
  for the MVP.
- The user-supplied `marginalia-logo.svg` is at the repo root —
  decide where the brand mark belongs (header? footer? `static/`?)
  during the Phase 3 reader pass.
- Confirm `flask --app statutrack.web` works as a local dev entry
  point (it should — `create_app` is exported from `statutrack.web`)
  and add a `make dev` or just a `Makefile` recipe if the team grows
  past one developer.
- Test isolation: a long-running ingest holding the WAL lock made
  one pytest run error with BlockingIOError. Tests already use
  per-test SQLite files under `tmp_path` so this is a contention
  artefact, not a correctness issue — but worth reconfirming once
  the tree is clean.

## Commit log (high-level)

```
d52f096  Phase 2c: section-level diff engine
6003ae4  Phase 2b: SQLite loader for parsed XML versions
eb94b6f  Phase 2a: laws-lois-xml git history walker
f466e9a  Redesign II: white-paper Wikipedia-esque + Word redlines
68f2c14  Redesign landing page: serif typography, revision-as-ink
b61488b  Stop tracking Passenger stderr.log + ignore runtime logs
b8de4e5  Phase 0 deploy: fix venv path, restore passenger_wsgi shim
c6ac894  Add real-world parser smoke test against the Bank Act XML
cc7fc72  Phase 1: extract statutrack.parser library
1a53a6c  Phase 0: bootstrap StatuTrack scaffold
```
