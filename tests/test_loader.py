"""Tests for the SQLite ingest loader.

Uses on-the-fly synthetic git history + a synthetic regulation XML
varied across commits, so the test is portable and doesn't depend on
the laws-lois-xml clone or a pre-populated SQLite file.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from statutrack.ingest.loader import (
    apply_schema,
    get_db,
    load_instrument_history,
)


# --------------------------------------------------------------------------
# Fixture: a fake laws-lois-xml repo with three substantive revisions of
# one regulation plus one metadata-only churn commit (same blob, new SHA).
# --------------------------------------------------------------------------

XML_V1 = """<?xml version="1.0" encoding="UTF-8"?>
<Regulation xmlns:lims="http://justice.gc.ca/lims" regulation-type="SOR" gazette-part="II">
  <Identification>
    <InstrumentNumber>SOR/2099-001</InstrumentNumber>
    <LongTitle>Synthetic Reporting Regulations</LongTitle>
    <ShortTitle>Reporting Regs</ShortTitle>
  </Identification>
  <Body>
    <Section lims:fid="s_1">
      <Label>1</Label>
      <MarginalNote>Records</MarginalNote>
      <Subsection lims:fid="s_1_1">
        <Label>(1)</Label>
        <Text>Reporting entities shall keep records for five years.</Text>
      </Subsection>
    </Section>
  </Body>
</Regulation>
"""

XML_V2 = XML_V1.replace("five years", "six years")
XML_V3 = XML_V2.replace("six years", "seven years")


def _git(repo: Path, *args: str, env_overrides: dict | None = None) -> None:
    env = {
        "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "t@t.test",
        "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "t@t.test",
        "PATH": "/usr/bin:/usr/local/bin",
    }
    if env_overrides:
        env.update(env_overrides)
    subprocess.run(["git", "-C", str(repo), *args], check=True, env=env,
                   capture_output=True)


def _commit(repo: Path, message: str, *, date: str) -> None:
    env = {"GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date}
    _git(repo, "add", "-A", env_overrides=env)
    _git(repo, "commit", "-m", message, env_overrides=env)


@pytest.fixture
def fake_clone(tmp_path: Path) -> Path:
    repo = tmp_path / "laws-lois-xml"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    target = repo / "eng" / "regulations" / "SOR-2099-001.xml"
    target.parent.mkdir(parents=True)

    target.write_text(XML_V1)
    _commit(repo, "v1: five years", date="2024-01-15T12:00:00+00:00")

    # Metadata-only churn: unrelated file changes, target untouched.
    (repo / "NOTES.md").write_text("housekeeping\n")
    _commit(repo, "metadata only", date="2024-02-15T12:00:00+00:00")

    target.write_text(XML_V2)
    _commit(repo, "v2: six years", date="2024-03-15T12:00:00+00:00")

    target.write_text(XML_V3)
    _commit(repo, "v3: seven years", date="2024-04-15T12:00:00+00:00")

    return repo


@pytest.fixture
def db(tmp_path: Path):
    conn = get_db(tmp_path / "statutrack.sqlite")
    apply_schema(conn)
    yield conn
    conn.close()


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------

def test_load_inserts_one_instrument_and_three_versions(fake_clone, db):
    report = load_instrument_history(
        db, fake_clone, "eng/regulations/SOR-2099-001.xml",
        slug="SOR-2099-001", language="eng",
    )
    assert report.versions_inserted == 3
    assert report.versions_skipped_dedup == 0  # walker already drops touch-only

    instrument_count = db.execute("SELECT COUNT(*) FROM instruments").fetchone()[0]
    version_count = db.execute("SELECT COUNT(*) FROM versions").fetchone()[0]
    assert instrument_count == 1
    assert version_count == 3


def test_loaded_instrument_carries_titles_and_type(fake_clone, db):
    load_instrument_history(
        db, fake_clone, "eng/regulations/SOR-2099-001.xml",
        slug="SOR-2099-001", language="eng",
    )
    row = db.execute(
        "SELECT slug, language, type, short_title, long_title, citation "
        "FROM instruments WHERE slug = ?",
        ("SOR-2099-001",),
    ).fetchone()
    assert row["slug"] == "SOR-2099-001"
    assert row["language"] == "eng"
    assert row["type"] == "regulation"
    assert row["short_title"] == "Reporting Regs"
    assert row["long_title"] == "Synthetic Reporting Regulations"
    assert row["citation"] == "SOR/2099-001"


def test_versions_ordered_oldest_first(fake_clone, db):
    load_instrument_history(
        db, fake_clone, "eng/regulations/SOR-2099-001.xml",
        slug="SOR-2099-001",
    )
    dates = [r["commit_date"] for r in db.execute(
        "SELECT commit_date FROM versions ORDER BY commit_date ASC"
    )]
    assert dates == sorted(dates)


def test_sections_persisted_with_version_link(fake_clone, db):
    load_instrument_history(
        db, fake_clone, "eng/regulations/SOR-2099-001.xml",
        slug="SOR-2099-001",
    )
    # Each version should have at least the section + subsection rows.
    rows = db.execute(
        """
        SELECT v.commit_date, s.content
          FROM versions v JOIN sections s ON s.version_id = v.id
         WHERE s.citation = 's. 1' OR s.citation = 's. 1(1)'
         ORDER BY v.commit_date ASC, s.ord ASC
        """
    ).fetchall()
    texts = " | ".join(r["content"] for r in rows)
    assert "five years" in texts
    assert "six years" in texts
    assert "seven years" in texts


def test_rerunning_is_idempotent(fake_clone, db):
    first = load_instrument_history(
        db, fake_clone, "eng/regulations/SOR-2099-001.xml",
        slug="SOR-2099-001",
    )
    second = load_instrument_history(
        db, fake_clone, "eng/regulations/SOR-2099-001.xml",
        slug="SOR-2099-001",
    )
    assert first.versions_inserted == 3
    assert second.versions_inserted == 0
    assert second.versions_skipped_existing == 3
    assert db.execute("SELECT COUNT(*) FROM versions").fetchone()[0] == 3


def test_diffs_table_populated_with_change_rows(fake_clone, db):
    """After ingesting three substantive versions, the loader should
    have written the inter-version diffs into ``diffs`` and skipped
    the unchanged rows."""
    load_instrument_history(
        db, fake_clone, "eng/regulations/SOR-2099-001.xml",
        slug="SOR-2099-001",
    )
    diff_rows = db.execute(
        "SELECT change_type, inline_html FROM diffs ORDER BY id"
    ).fetchall()

    # Two version pairs (v1->v2, v2->v3) each produce exactly one
    # modified diff on the single subsection ("s. 1(1)") whose text
    # changes between versions. No 'unchanged' rows should be present.
    assert all(r["change_type"] != "unchanged" for r in diff_rows)
    modified = [r for r in diff_rows if r["change_type"] == "modified"]
    assert len(modified) == 2

    htmls = " ".join(r["inline_html"] for r in modified)
    assert "<del>five</del>" in htmls
    assert "<ins>six</ins>" in htmls
    assert "<del>six</del>" in htmls
    assert "<ins>seven</ins>" in htmls


def test_diff_persistence_is_idempotent(fake_clone, db):
    load_instrument_history(
        db, fake_clone, "eng/regulations/SOR-2099-001.xml",
        slug="SOR-2099-001",
    )
    first_count = db.execute("SELECT COUNT(*) FROM diffs").fetchone()[0]
    # Re-run; the UNIQUE constraint on (from, to, citation) should
    # absorb every re-insert, leaving the row count unchanged.
    load_instrument_history(
        db, fake_clone, "eng/regulations/SOR-2099-001.xml",
        slug="SOR-2099-001",
    )
    second_count = db.execute("SELECT COUNT(*) FROM diffs").fetchone()[0]
    assert first_count == second_count


def test_blob_dedup_skips_byte_identical_revision(fake_clone, db, monkeypatch):
    """The loader's blob-level dedup collapses two FileVersion entries
    that share a blob SHA into a single ``versions`` row.

    Git itself refuses to produce two real commits with identical
    blobs against the same path (the staging area treats them as a
    no-op), so we exercise this branch by patching the walker to emit
    a duplicate blob mid-stream. The real-world trigger — Justice
    Canada's "Laws Site Update" commits that re-emit a file whose
    bytes don't change — is covered by the PCMLTFR smoke ingest, which
    actually has two first-day versions sharing one blob SHA."""
    from statutrack.ingest import loader as loader_mod
    from statutrack.ingest.walker import FileVersion

    real = loader_mod.list_file_versions(
        fake_clone, "eng/regulations/SOR-2099-001.xml",
    )
    # Splice in a fake "metadata-only" commit that carries the same
    # blob as the previous version. The loader should skip it.
    spliced = list(real)
    twin = real[1]
    spliced.insert(2, FileVersion(
        commit_hash="f" * 40,
        commit_date="2024-03-15T18:00:00+00:00",
        path=twin.path,
        blob_sha=twin.blob_sha,
    ))
    monkeypatch.setattr(loader_mod, "list_file_versions",
                        lambda *a, **kw: spliced)

    report = load_instrument_history(
        db, fake_clone, "eng/regulations/SOR-2099-001.xml",
        slug="SOR-2099-001",
    )
    assert report.versions_inserted == 3
    assert report.versions_skipped_dedup == 1
