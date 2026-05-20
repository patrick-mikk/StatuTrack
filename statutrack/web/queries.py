"""SQL queries for the Flask layer.

Kept in one module so the route handlers stay shape-only and so the
SQL stays read-only by construction — the web tier never writes; only
the ingest pipeline does. All returned values are plain dicts (built
from sqlite3.Row) so Jinja templates can attribute-access them
without an ORM in the picture.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


@dataclass(frozen=True)
class InstrumentRow:
    id: int
    slug: str
    language: str
    type: str
    short_title: str
    long_title: str
    citation: str
    enabling_act: str
    latest_commit_date: str | None = None
    latest_last_amended: str | None = None
    version_count: int = 0


@dataclass(frozen=True)
class VersionRow:
    id: int
    commit_hash: str
    commit_date: str
    last_amended: str | None
    section_count: int


@dataclass(frozen=True)
class SectionRow:
    id: int
    citation: str
    section_number: str
    subsection: str | None
    paragraph: str | None
    marginal_note: str | None
    heading_path: str | None
    content: str
    ord: int


# ---------------------------------------------------------------------------
# Connection helpers
# ---------------------------------------------------------------------------

@contextmanager
def open_db(path: str | Path) -> Iterator[sqlite3.Connection]:
    """Open the StatuTrack SQLite file read-only and yield a connection
    with ``sqlite3.Row`` row factory enabled.

    ``mode=ro`` plus the URI scheme means an accidental ``INSERT``
    from a route handler raises immediately rather than mutating the
    file the ingest worker is writing to.
    """
    conn = sqlite3.connect(f"file:{Path(path)}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------

def list_instruments(conn: sqlite3.Connection,
                     *,
                     language: str = "eng") -> list[InstrumentRow]:
    """All ingested instruments for one language, sorted by short title.

    Each row carries the latest version's commit date and last-amended
    date so the browse view can show recency without a second
    round-trip per instrument.
    """
    rows = conn.execute(
        """
        SELECT i.id, i.slug, i.language, i.type, i.short_title,
               i.long_title, i.citation, i.enabling_act,
               (SELECT commit_date FROM versions v
                 WHERE v.instrument_id = i.id
                 ORDER BY commit_date DESC LIMIT 1) AS latest_commit_date,
               (SELECT last_amended FROM versions v
                 WHERE v.instrument_id = i.id
                 ORDER BY commit_date DESC LIMIT 1) AS latest_last_amended,
               (SELECT COUNT(*) FROM versions v WHERE v.instrument_id = i.id) AS version_count
          FROM instruments i
         WHERE i.language = ?
         ORDER BY i.short_title ASC
        """,
        (language,),
    )
    return [
        InstrumentRow(
            id=r["id"], slug=r["slug"], language=r["language"],
            type=r["type"], short_title=r["short_title"],
            long_title=r["long_title"], citation=r["citation"],
            enabling_act=r["enabling_act"],
            latest_commit_date=r["latest_commit_date"],
            latest_last_amended=r["latest_last_amended"],
            version_count=r["version_count"],
        )
        for r in rows
    ]


def find_instrument(conn: sqlite3.Connection,
                    slug: str,
                    *,
                    language: str = "eng") -> InstrumentRow | None:
    row = conn.execute(
        "SELECT id, slug, language, type, short_title, long_title, "
        "       citation, enabling_act "
        "  FROM instruments WHERE slug = ? AND language = ?",
        (slug, language),
    ).fetchone()
    if row is None:
        return None
    return InstrumentRow(
        id=row["id"], slug=row["slug"], language=row["language"],
        type=row["type"], short_title=row["short_title"],
        long_title=row["long_title"], citation=row["citation"],
        enabling_act=row["enabling_act"],
    )


def list_versions(conn: sqlite3.Connection,
                  instrument_id: int) -> list[VersionRow]:
    rows = conn.execute(
        """
        SELECT v.id, v.commit_hash, v.commit_date, v.last_amended,
               (SELECT COUNT(*) FROM sections s WHERE s.version_id = v.id) AS section_count
          FROM versions v
         WHERE v.instrument_id = ?
         ORDER BY v.commit_date DESC, v.id DESC
        """,
        (instrument_id,),
    )
    return [
        VersionRow(
            id=r["id"], commit_hash=r["commit_hash"],
            commit_date=r["commit_date"], last_amended=r["last_amended"],
            section_count=r["section_count"],
        )
        for r in rows
    ]


def latest_version(conn: sqlite3.Connection,
                   instrument_id: int) -> VersionRow | None:
    versions = list_versions(conn, instrument_id)
    return versions[0] if versions else None


@dataclass(frozen=True)
class DiffRow:
    id: int
    citation: str
    change_type: str
    inline_html: str
    old_section_id: int | None
    new_section_id: int | None


def find_version(conn: sqlite3.Connection,
                 instrument_id: int,
                 version_id: int) -> VersionRow | None:
    row = conn.execute(
        """
        SELECT v.id, v.commit_hash, v.commit_date, v.last_amended,
               (SELECT COUNT(*) FROM sections s WHERE s.version_id = v.id) AS section_count
          FROM versions v
         WHERE v.instrument_id = ? AND v.id = ?
        """,
        (instrument_id, version_id),
    ).fetchone()
    if row is None:
        return None
    return VersionRow(
        id=row["id"], commit_hash=row["commit_hash"],
        commit_date=row["commit_date"], last_amended=row["last_amended"],
        section_count=row["section_count"],
    )


def list_diffs(conn: sqlite3.Connection,
               from_version_id: int,
               to_version_id: int) -> list[DiffRow]:
    """Diffs between two arbitrary versions of the same instrument.

    Only change rows (added / removed / modified / renumbered) are
    persisted; sections without a row are unchanged by construction.
    """
    rows = conn.execute(
        """
        SELECT id, citation, change_type, inline_html,
               old_section_id, new_section_id
          FROM diffs
         WHERE from_version_id = ? AND to_version_id = ?
         ORDER BY id ASC
        """,
        (from_version_id, to_version_id),
    )
    return [
        DiffRow(
            id=r["id"], citation=r["citation"],
            change_type=r["change_type"], inline_html=r["inline_html"],
            old_section_id=r["old_section_id"],
            new_section_id=r["new_section_id"],
        )
        for r in rows
    ]


def list_sections(conn: sqlite3.Connection,
                  version_id: int) -> list[SectionRow]:
    rows = conn.execute(
        """
        SELECT id, citation, section_number, subsection, paragraph,
               marginal_note, heading_path, content, ord
          FROM sections
         WHERE version_id = ?
         ORDER BY ord ASC
        """,
        (version_id,),
    )
    return [
        SectionRow(
            id=r["id"], citation=r["citation"],
            section_number=r["section_number"],
            subsection=r["subsection"], paragraph=r["paragraph"],
            marginal_note=r["marginal_note"],
            heading_path=r["heading_path"], content=r["content"],
            ord=r["ord"],
        )
        for r in rows
    ]
