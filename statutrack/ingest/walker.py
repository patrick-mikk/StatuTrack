"""Enumerate the version history of a single file in the laws-lois-xml repo.

The git history of ``justicecanada/laws-lois-xml`` is treated as the
canonical version archive of Canadian federal law. For each XML file
of interest, this module returns the list of commits that ever touched
it — chronologically — along with the blob SHA and committed date at
each commit. Reading a specific version's XML is done through
``read_blob`` (which shells out to ``git cat-file blob``), so this works
even before the working tree has finished checking out, and even on
commits where the file has been renamed or deleted further down the
history.

Used by :mod:`statutrack.ingest.loader` to drive the per-file ingest
loop. Pure metadata — no parsing here.
"""
from __future__ import annotations

import datetime as dt
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class FileVersion:
    """One commit's view of one XML file inside the laws-lois-xml repo.

    ``commit_hash`` is the full 40-char SHA. ``path`` is the path the
    file held at *that* commit (with ``--follow`` enabled it may differ
    from the current path if the file was renamed). ``blob_sha`` is the
    git blob SHA for the file content at that commit — useful as a
    cheap unchanged-content check (two commits with the same blob SHA
    have identical bytes, no parsing needed). ``commit_date`` is the
    committer date in UTC ISO-8601 ("YYYY-MM-DDTHH:MM:SS+00:00"); the
    Justice Canada commits don't typically carry an author/committer
    distinction worth preserving here.
    """
    commit_hash: str
    commit_date: str
    path: str
    blob_sha: str


def _run_git(repo: Path, *args: str) -> str:
    """Run a git command inside ``repo`` and return its stdout as text."""
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        errors="replace",
    )
    return completed.stdout


def list_file_versions(repo: Path, file_path: str) -> list[FileVersion]:
    """Return every commit that ever touched ``file_path`` inside ``repo``.

    Versions come back oldest-first so the caller can walk them in the
    same order as the document's amendment history. ``--follow``
    threads through renames so a file moved between directories
    upstream stays one continuous timeline.

    Commits where the file was *deleted* are excluded — they tell us
    nothing useful about the consolidated text, and including them
    would force the diff engine to emit a "removed" placeholder for
    every previous section, which is noise rather than signal.
    """
    # %H = full SHA, %cI = committer date in strict ISO-8601.
    # ``--name-only`` gives us the path the file held at each commit,
    # which matters when ``--follow`` chases through renames.
    #
    # We intentionally do NOT pass ``--reverse``: ``git log --follow
    # --reverse`` is documented-as-buggy (returns only the rename
    # commit in our smoke test against a synthetic repo), so we walk
    # newest-first and reverse in Python at the end.
    raw = _run_git(
        repo,
        "log",
        "--follow",
        "--no-merges",
        "--format=__COMMIT__ %H %cI",
        "--name-only",
        "--",
        file_path,
    )

    versions: list[FileVersion] = []
    cur_hash = ""
    cur_date = ""
    for line in raw.splitlines():
        if not line:
            continue
        if line.startswith("__COMMIT__ "):
            parts = line.split(" ", 2)
            cur_hash = parts[1]
            cur_date = parts[2]
            continue
        # Anything that isn't the header is one of the paths touched
        # by that commit. With ``-- <file>`` restricting the scope,
        # log only emits paths matching that pathspec, but ``--follow``
        # may emit the file under both its old and new names within
        # the same rename commit. Take the first occurrence per
        # commit — that's the path the file held at this revision.
        cur_path = line.strip()
        if any(v.commit_hash == cur_hash for v in versions):
            continue
        blob_sha = _blob_sha(repo, cur_hash, cur_path)
        if blob_sha is None:
            # File was deleted at this commit (rename-old-name in a
            # rename commit lands here too); skip.
            continue
        versions.append(FileVersion(
            commit_hash=cur_hash,
            commit_date=cur_date,
            path=cur_path,
            blob_sha=blob_sha,
        ))

    # ``git log`` walks newest-first; flip to chronological order so
    # the caller can stream commits in the same order as the
    # document's amendment history.
    versions.reverse()
    return versions


def _blob_sha(repo: Path, commit: str, path: str) -> str | None:
    """Return the git blob SHA for ``path`` at ``commit``, or None if absent."""
    try:
        out = _run_git(repo, "ls-tree", commit, "--", path)
    except subprocess.CalledProcessError:
        return None
    # ls-tree output: "<mode> <type> <sha>\t<path>" — one line per match.
    if not out.strip():
        return None
    first = out.splitlines()[0]
    parts = first.split()
    if len(parts) < 3 or parts[1] != "blob":
        return None
    return parts[2]


def read_blob(repo: Path, blob_sha: str) -> bytes:
    """Fetch the raw bytes of a git blob — used to read an XML version
    without depending on the working tree checkout being finished."""
    completed = subprocess.run(
        ["git", "-C", str(repo), "cat-file", "blob", blob_sha],
        check=True,
        capture_output=True,
    )
    return completed.stdout


def parse_commit_date(iso_8601: str) -> dt.datetime:
    """Parse a strict ISO-8601 datetime — the committer date format
    ``git log %cI`` emits — into a timezone-aware ``datetime``."""
    return dt.datetime.fromisoformat(iso_8601)
