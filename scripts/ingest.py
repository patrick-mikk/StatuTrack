#!/usr/bin/env python3
"""StatuTrack ingest CLI.

Drives :func:`statutrack.ingest.loader.load_instrument_history` against
the local laws-lois-xml clone and writes versions + sections + diffs
into the SQLite database. Designed for two callers:

  * Interactive: ``python scripts/ingest.py --slug SOR-2002-184``
    when iterating on one instrument.

  * Cron: ``python scripts/ingest.py --all`` once a night to pick up
    upstream consolidation updates. The loader is idempotent so this
    is safe to run repeatedly; only new commits do any real work.

Paths default to the values used by ``scripts/bootstrap.sh``:

  * ``$STATUTRACK_DB``       → ~/data/statutrack/statutrack.sqlite
  * ``$STATUTRACK_LAWS_XML`` → ~/data/laws-lois-xml

Override either on the command line if you're running against a
non-default location.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
from pathlib import Path

# Make sure the project root is on sys.path so this script works whether
# invoked as ``python scripts/ingest.py`` (cron) or ``python -m
# scripts.ingest`` (manual).
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from statutrack.ingest.loader import (
    apply_schema,
    get_db,
    load_instrument_history,
)

DEFAULT_DB = Path(os.environ.get(
    "STATUTRACK_DB", str(Path.home() / "data" / "statutrack" / "statutrack.sqlite"),
))
DEFAULT_REPO = Path(os.environ.get(
    "STATUTRACK_LAWS_XML", str(Path.home() / "data" / "laws-lois-xml"),
))


# --------------------------------------------------------------------------
# File-path discovery
# --------------------------------------------------------------------------

ENG_INSTRUMENT_DIRS = ("eng/acts", "eng/regulations")
FRA_INSTRUMENT_DIRS = ("fra/lois", "fra/reglements")


def _slug_for(path: str) -> str:
    """Derive the StatuTrack slug for an XML file path inside laws-lois-xml.

    Examples
    --------
    ``eng/regulations/SOR-2002-184.xml`` -> ``SOR-2002-184``
    ``eng/acts/B-1.01.xml``              -> ``B-1.01``
    """
    return Path(path).stem


def _resolve_paths(args: argparse.Namespace) -> list[tuple[str, str, str]]:
    """Return ``[(file_path, slug, language), ...]`` based on CLI args.

    Path resolution rules:
      * ``--slug SOR-2002-184`` tries both eng/regulations and eng/acts
        until it finds the file. ``--language`` narrows the search.
      * ``--path eng/regulations/SOR-2002-184.xml`` is taken literally.
      * ``--all`` discovers every .xml under the language directories
        in the laws-lois-xml clone via ``git ls-tree HEAD`` (works even
        when the working tree isn't fully checked out).
    """
    repo = args.repo
    language = args.language

    if args.path:
        return [(args.path, args.slug or _slug_for(args.path), language)]

    if args.slug:
        candidate_dirs = ENG_INSTRUMENT_DIRS if language == "eng" else FRA_INSTRUMENT_DIRS
        for d in candidate_dirs:
            path = f"{d}/{args.slug}.xml"
            if _path_in_git(repo, path):
                return [(path, args.slug, language)]
        sys.exit(f"error: could not find {args.slug}.xml under {candidate_dirs} in {repo}")

    if args.all:
        candidate_dirs = ENG_INSTRUMENT_DIRS if language == "eng" else FRA_INSTRUMENT_DIRS
        paths: list[tuple[str, str, str]] = []
        for d in candidate_dirs:
            for p in _ls_tree(repo, d):
                if p.endswith(".xml"):
                    paths.append((p, _slug_for(p), language))
        return sorted(paths)

    sys.exit("error: specify one of --slug, --path, or --all")


def _path_in_git(repo: Path, path: str) -> bool:
    out = subprocess.run(
        ["git", "-C", str(repo), "ls-tree", "HEAD", "--", path],
        capture_output=True, text=True,
    )
    return out.returncode == 0 and out.stdout.strip() != ""


def _ls_tree(repo: Path, prefix: str) -> list[str]:
    out = subprocess.run(
        ["git", "-C", str(repo), "ls-tree", "-r", "--name-only", "HEAD", "--", prefix],
        capture_output=True, text=True, check=True,
    )
    return [line for line in out.stdout.splitlines() if line.strip()]


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Ingest XML versions from laws-lois-xml into StatuTrack."
    )
    group = parser.add_mutually_exclusive_group(required=False)
    group.add_argument(
        "--slug", help="Ingest one instrument by slug (e.g. SOR-2002-184).",
    )
    group.add_argument(
        "--path",
        help="Ingest one instrument by repo-relative XML path "
             "(e.g. eng/regulations/SOR-2002-184.xml).",
    )
    group.add_argument(
        "--all", action="store_true",
        help="Ingest every instrument under eng/acts and eng/regulations "
             "(or the French equivalents with --language fra).",
    )
    parser.add_argument("--language", choices=("eng", "fra"), default="eng")
    parser.add_argument(
        "--repo", type=Path, default=DEFAULT_REPO,
        help=f"Path to the laws-lois-xml clone (default: {DEFAULT_REPO}).",
    )
    parser.add_argument(
        "--db", type=Path, default=DEFAULT_DB,
        help=f"Path to the StatuTrack SQLite file (default: {DEFAULT_DB}).",
    )
    parser.add_argument(
        "--max", type=int, default=0,
        help="Optional cap on instruments processed (for smoke runs).",
    )
    args = parser.parse_args()

    targets = _resolve_paths(args)
    if args.max:
        targets = targets[: args.max]

    args.db.parent.mkdir(parents=True, exist_ok=True)
    conn = get_db(args.db)
    apply_schema(conn)

    total_versions = 0
    total_sections = 0
    total_diffs = 0
    failures = 0
    started = time.monotonic()

    print(f"[ingest] {len(targets)} instrument(s) -> {args.db}")
    for i, (path, slug, language) in enumerate(targets, 1):
        t0 = time.monotonic()
        try:
            report = load_instrument_history(
                conn, args.repo, path, slug=slug, language=language,
            )
        except Exception as exc:  # noqa: BLE001 — top-level CLI handler
            print(f"  [{i}/{len(targets)}] {slug:<20} FAILED: {exc!r}")
            failures += 1
            continue
        elapsed = time.monotonic() - t0
        total_versions += report.versions_inserted
        total_sections += report.sections_inserted
        total_diffs += report.diffs_inserted
        print(
            f"  [{i}/{len(targets)}] {slug:<20} "
            f"+{report.versions_inserted:>2}v "
            f"+{report.sections_inserted:>5}s "
            f"+{report.diffs_inserted:>5}d "
            f"({elapsed:>5.1f}s)"
        )

    conn.close()
    print(
        f"[ingest] done in {time.monotonic() - started:.1f}s — "
        f"{total_versions} versions, {total_sections} sections, "
        f"{total_diffs} diffs"
        + (f", {failures} failures" if failures else "")
    )
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
