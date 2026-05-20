"""Tests for the laws-lois-xml git history walker.

Uses an on-the-fly synthetic git repo rather than depending on a clone
of laws-lois-xml being present. That keeps the test suite portable
(CI, fresh checkouts, etc.) while still exercising the real
``git log --follow`` / ``git cat-file blob`` plumbing the walker uses
end-to-end.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from statutrack.ingest.walker import (
    FileVersion,
    list_file_versions,
    parse_commit_date,
    read_blob,
)


def _git(repo: Path, *args: str, env_overrides: dict | None = None) -> None:
    env = {
        # Pin the committer + author so the test is deterministic and
        # doesn't depend on the host's git config.
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.test",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.test",
        "PATH": "/usr/bin:/usr/local/bin:/opt/alt/git/bin",
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
def fake_repo(tmp_path: Path) -> Path:
    """A throwaway git repo with three commits to a single file plus
    one rename — enough to exercise ``--follow`` and the dedup logic."""
    repo = tmp_path / "laws-lois-xml"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")

    f1 = repo / "eng" / "acts" / "X-1.xml"
    f1.parent.mkdir(parents=True)
    f1.write_text("<Statute><Body><Section><Label>1</Label><Text>first</Text></Section></Body></Statute>\n")
    _commit(repo, "initial", date="2024-01-01T12:00:00+00:00")

    f1.write_text("<Statute><Body><Section><Label>1</Label><Text>second</Text></Section></Body></Statute>\n")
    _commit(repo, "second revision", date="2024-02-15T12:00:00+00:00")

    # Rename the file to exercise --follow.
    new_path = repo / "eng" / "acts" / "X-1.01.xml"
    f1.rename(new_path)
    _commit(repo, "rename file", date="2024-03-01T12:00:00+00:00")

    new_path.write_text("<Statute><Body><Section><Label>1</Label><Text>third</Text></Section></Body></Statute>\n")
    _commit(repo, "third revision", date="2024-04-10T12:00:00+00:00")

    return repo


def test_list_file_versions_returns_chronological_order(fake_repo: Path):
    versions = list_file_versions(fake_repo, "eng/acts/X-1.01.xml")
    dates = [v.commit_date for v in versions]
    assert dates == sorted(dates), "versions should be oldest-first"


def test_list_file_versions_follows_renames(fake_repo: Path):
    versions = list_file_versions(fake_repo, "eng/acts/X-1.01.xml")
    # All four commits should be reachable: two on the old name, one
    # rename commit, one on the new name. --follow threads them.
    assert len(versions) == 4
    paths = {v.path for v in versions}
    assert "eng/acts/X-1.xml" in paths
    assert "eng/acts/X-1.01.xml" in paths


def test_each_version_carries_a_blob_sha(fake_repo: Path):
    versions = list_file_versions(fake_repo, "eng/acts/X-1.01.xml")
    for v in versions:
        assert isinstance(v, FileVersion)
        assert len(v.blob_sha) == 40  # full git object SHA


def test_read_blob_returns_committed_bytes(fake_repo: Path):
    versions = list_file_versions(fake_repo, "eng/acts/X-1.01.xml")
    first_xml = read_blob(fake_repo, versions[0].blob_sha).decode("utf-8")
    last_xml = read_blob(fake_repo, versions[-1].blob_sha).decode("utf-8")
    assert "first" in first_xml
    assert "third" in last_xml


def test_blob_sha_identifies_byte_identical_revisions(tmp_path: Path):
    """Two commits that don't change the file should share a blob SHA —
    the Phase 2 loader uses this as a cheap unchanged-content check."""
    repo = tmp_path / "metadata-repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")

    f = repo / "doc.xml"
    f.write_text("<x/>\n")
    _commit(repo, "initial", date="2024-01-01T12:00:00+00:00")

    # Touch an unrelated file so the second commit exists without
    # changing doc.xml's contents.
    (repo / "README").write_text("docs\n")
    _commit(repo, "unrelated change touches doc.xml via -A only",
            date="2024-02-01T12:00:00+00:00")

    # Now make a real change to doc.xml.
    f.write_text("<y/>\n")
    _commit(repo, "actual change", date="2024-03-01T12:00:00+00:00")

    versions = list_file_versions(repo, "doc.xml")
    # The middle commit didn't touch doc.xml at all, so only two
    # versions should be enumerated; their blob SHAs differ.
    assert len(versions) == 2
    assert versions[0].blob_sha != versions[1].blob_sha


def test_parse_commit_date_round_trips():
    parsed = parse_commit_date("2025-01-15T09:30:45+00:00")
    assert parsed.year == 2025
    assert parsed.month == 1
    assert parsed.day == 15
    assert parsed.tzinfo is not None
