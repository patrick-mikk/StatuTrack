"""Persist parsed XML versions into the StatuTrack SQLite database.

The loader stitches together :mod:`statutrack.ingest.walker` (enumerate
every commit that touched an XML file) and
:func:`statutrack.parser.parse_document` (turn one commit's bytes into
a structured :class:`~statutrack.parser.ParsedDocument`), then writes
the result into the schema defined in
:mod:`statutrack.db.schema`.

Two optimisations matter for the cron-driven nightly refresh:

1. **Blob-level dedup.** Justice Canada's repo gets routine "Laws Site
   Update" commits that don't touch every file — even when they do
   touch a file's mtime metadata, the file content (and therefore the
   git blob SHA) is identical. We persist ``blob_sha`` on
   :class:`versions` and skip writing a new version row when the
   previous version of the same instrument already carries that blob;
   reading the existing row out of SQLite is O(microseconds) versus
   re-parsing several hundred KB of XML.

2. **Idempotent per-version transaction.** Each ``(instrument,
   commit_hash)`` pair is unique. The loader wraps the version insert
   plus every section insert in one transaction, so a kill mid-ingest
   leaves the DB in a consistent state and the next run picks up
   exactly where it stopped.
"""
from __future__ import annotations

import datetime as dt
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from statutrack.parser import ParsedDocument, parse_document

from .walker import FileVersion, list_file_versions, read_blob

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "db" / "schema.sql"


# ---------------------------------------------------------------------------
# Connection helpers
# ---------------------------------------------------------------------------

def get_db(path: str | Path) -> sqlite3.Connection:
    """Open a connection at ``path`` with the pragmas StatuTrack needs."""
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def apply_schema(conn: sqlite3.Connection, schema_sql: str | None = None) -> None:
    """Apply ``db/schema.sql`` to ``conn``. Idempotent — every CREATE
    in the schema uses ``IF NOT EXISTS``."""
    sql = schema_sql if schema_sql is not None else SCHEMA_PATH.read_text()
    conn.executescript(sql)


# ---------------------------------------------------------------------------
# Row mappers
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LoadReport:
    """Summary of what one call to :func:`load_instrument_history` did.

    The CLI uses this for the per-instrument status line in ingest
    logs; tests assert on it to confirm dedup behaviour without
    digging into the database.
    """
    instrument_id: int
    versions_inserted: int
    versions_skipped_dedup: int
    versions_skipped_existing: int
    sections_inserted: int


def _upsert_instrument(conn: sqlite3.Connection, *,
                       slug: str,
                       language: str,
                       parsed: ParsedDocument) -> int:
    """Insert or update the ``instruments`` row for this slug+language
    pair, returning its primary key."""
    citation = parsed.instrument_number or parsed.formal_citation or ""
    enabling = ""
    if parsed.document_type == "regulation" and parsed.enabling_authorities:
        enabling = parsed.enabling_authorities[0].title

    row = conn.execute(
        "SELECT id FROM instruments WHERE slug = ? AND language = ?",
        (slug, language),
    ).fetchone()
    if row is None:
        cur = conn.execute(
            """
            INSERT INTO instruments
                (slug, language, type, short_title, long_title,
                 citation, enabling_act)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (slug, language, parsed.document_type,
             parsed.short_title or parsed.long_title or slug,
             parsed.long_title or "",
             citation,
             enabling),
        )
        return cur.lastrowid

    # Keep titles in sync with the latest version we've seen — the
    # short title in particular changes wording occasionally upstream.
    conn.execute(
        """
        UPDATE instruments
           SET type = ?, short_title = ?, long_title = ?,
               citation = ?, enabling_act = ?
         WHERE id = ?
        """,
        (parsed.document_type,
         parsed.short_title or parsed.long_title or slug,
         parsed.long_title or "",
         citation,
         enabling,
         row["id"]),
    )
    return row["id"]


def _insert_version(conn: sqlite3.Connection, *,
                    instrument_id: int,
                    fv: FileVersion,
                    parsed: ParsedDocument) -> int:
    cur = conn.execute(
        """
        INSERT INTO versions
            (instrument_id, commit_hash, commit_date, blob_sha,
             pit_date, last_amended, parsed_at, xml_path)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (instrument_id,
         fv.commit_hash,
         fv.commit_date,
         fv.blob_sha,
         parsed.pit_date or None,
         parsed.last_amended_date or None,
         dt.datetime.now(tz=dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00"),
         fv.path),
    )
    return cur.lastrowid


def _insert_sections(conn: sqlite3.Connection,
                     version_id: int,
                     parsed: ParsedDocument) -> int:
    """Write one row per ProvisionRow into ``sections``. The schema
    name is a slight misnomer — provisions covers every level of the
    hierarchy (Section / Subsection / Paragraph / …), and we want
    every level searchable and diff-alignable."""
    rows = []
    for ord_, p in enumerate(parsed.provisions):
        heading_path = " > ".join(part for part in (p.part, p.division, p.subdivision) if part)
        rows.append((
            version_id,
            p.citation,
            p.section,
            p.subsection or None,
            p.paragraph or None,
            p.subparagraph or None,
            p.clause or None,
            p.marginal_note or None,
            heading_path or None,
            p.text,
            p.in_force_date or None,
            p.fid or None,
            ord_,
        ))
    conn.executemany(
        """
        INSERT INTO sections
            (version_id, citation, section_number, subsection,
             paragraph, subparagraph, clause, marginal_note,
             heading_path, content, in_force_date, fid, ord)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    return len(rows)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def load_instrument_history(conn: sqlite3.Connection,
                            repo: Path,
                            file_path: str,
                            *,
                            slug: str,
                            language: str = "eng") -> LoadReport:
    """Walk the laws-lois-xml history for ``file_path`` and persist
    every substantive version to ``conn``.

    ``slug`` is the StatuTrack identifier for the instrument
    (typically the filename stem, e.g. ``B-1.01`` or ``SOR-2002-184``)
    — it's the routing key the Flask layer uses in URLs.

    Re-running against the same database is safe: existing
    ``(instrument_id, commit_hash)`` pairs are skipped on the
    ``UNIQUE`` constraint, and blob-level dedup keeps metadata-only
    commits from generating noise rows.
    """
    versions = list_file_versions(repo, file_path)
    instrument_id: int | None = None
    last_blob_seen: str | None = None
    inserted = 0
    skipped_existing = 0
    skipped_dedup = 0
    sections_total = 0

    for fv in versions:
        # ---- Blob-level dedup against the previous chronological version.
        # The walker returns versions oldest-first, so ``last_blob_seen``
        # is the SHA of the last *kept* version. A new commit with the
        # same SHA is metadata-only and gets dropped without parsing.
        if last_blob_seen == fv.blob_sha:
            skipped_dedup += 1
            continue

        # ---- Cheap existing-row check. If we've already ingested this
        # exact (instrument, commit) pair on a previous run, skip the
        # parse + insert entirely. Once we know the instrument_id we
        # use it; until then, fall back to a slug+language lookup.
        if instrument_id is not None and _version_exists(conn, instrument_id, fv.commit_hash):
            skipped_existing += 1
            last_blob_seen = fv.blob_sha
            continue

        xml_bytes = read_blob(repo, fv.blob_sha)
        parsed = parse_document(
            xml_bytes,
            source_url=f"file://{repo / fv.path}",
        )

        with conn:  # transactional per-version
            if instrument_id is None:
                instrument_id = _upsert_instrument(
                    conn, slug=slug, language=language, parsed=parsed,
                )
                # Re-check for an existing version row using the now-known id.
                if _version_exists(conn, instrument_id, fv.commit_hash):
                    skipped_existing += 1
                    last_blob_seen = fv.blob_sha
                    continue
            version_id = _insert_version(
                conn, instrument_id=instrument_id, fv=fv, parsed=parsed,
            )
            sections_total += _insert_sections(conn, version_id, parsed)
            inserted += 1

        last_blob_seen = fv.blob_sha

    if instrument_id is None:
        raise ValueError(
            f"no versions found for {file_path} in {repo} — "
            "is the path correct and tracked by git?"
        )

    return LoadReport(
        instrument_id=instrument_id,
        versions_inserted=inserted,
        versions_skipped_dedup=skipped_dedup,
        versions_skipped_existing=skipped_existing,
        sections_inserted=sections_total,
    )


def _version_exists(conn: sqlite3.Connection,
                    instrument_id: int,
                    commit_hash: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM versions WHERE instrument_id = ? AND commit_hash = ?",
        (instrument_id, commit_hash),
    ).fetchone()
    return row is not None
