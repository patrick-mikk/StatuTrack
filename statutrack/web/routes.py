"""HTTP routes for StatuTrack.

Phase 3 lights up the browse and reader paths. The diff view follows
in Phase 4; until then ``/instruments/<slug>/compare`` 404s.

All database access goes through :mod:`statutrack.web.queries` so this
module stays shape-only and the routes can be argued about without
reading SQL.
"""
from __future__ import annotations

from pathlib import Path

from flask import (
    Blueprint, abort, current_app, render_template,
)

from . import queries

bp = Blueprint("statutrack", __name__)


# ---------------------------------------------------------------------------
# Browse + landing
# ---------------------------------------------------------------------------

@bp.get("/")
def index():
    db_path = Path(current_app.config["DATABASE_PATH"])
    instruments: list[queries.InstrumentRow] = []
    if db_path.exists():
        with queries.open_db(db_path) as conn:
            instruments = queries.list_instruments(conn)
    return render_template(
        "index.html",
        instruments=instruments,
        db_path=current_app.config["DATABASE_PATH"],
        laws_xml_path=current_app.config["LAWS_XML_PATH"],
    )


# ---------------------------------------------------------------------------
# Reader view
# ---------------------------------------------------------------------------

@bp.get("/instruments/<slug>")
def instrument(slug: str):
    """Reader view of the current consolidated version of one instrument."""
    db_path = Path(current_app.config["DATABASE_PATH"])
    if not db_path.exists():
        abort(404)

    with queries.open_db(db_path) as conn:
        inst = queries.find_instrument(conn, slug)
        if inst is None:
            abort(404)
        version = queries.latest_version(conn, inst.id)
        if version is None:
            abort(404)
        sections = queries.list_sections(conn, version.id)

    return render_template(
        "instrument.html",
        instrument=inst,
        version=version,
        sections=sections,
    )


@bp.get("/instruments/<slug>/versions")
def versions(slug: str):
    """Version timeline for one instrument."""
    db_path = Path(current_app.config["DATABASE_PATH"])
    if not db_path.exists():
        abort(404)

    with queries.open_db(db_path) as conn:
        inst = queries.find_instrument(conn, slug)
        if inst is None:
            abort(404)
        version_rows = queries.list_versions(conn, inst.id)

    return render_template(
        "versions.html",
        instrument=inst,
        versions=version_rows,
    )


@bp.get("/instruments/<slug>/compare")
def compare(slug: str):
    """Inline diff between two versions of one instrument.

    Query params:
      * ``from`` — older version id
      * ``to``   — newer version id

    Both must belong to ``slug``; otherwise 404. Without query params
    the route defaults to "previous → latest" so the most common
    "what changed last consolidation" question is one click away.
    """
    from flask import request

    db_path = Path(current_app.config["DATABASE_PATH"])
    if not db_path.exists():
        abort(404)

    try:
        from_id = int(request.args.get("from")) if request.args.get("from") else None
        to_id = int(request.args.get("to")) if request.args.get("to") else None
    except ValueError:
        abort(400)

    with queries.open_db(db_path) as conn:
        inst = queries.find_instrument(conn, slug)
        if inst is None:
            abort(404)
        all_versions = queries.list_versions(conn, inst.id)
        if len(all_versions) < 2:
            abort(404)

        # Default: previous -> latest. ``list_versions`` returns newest
        # first so ``all_versions[1]`` is the version immediately before
        # the current one.
        if from_id is None:
            from_id = all_versions[1].id
        if to_id is None:
            to_id = all_versions[0].id

        from_v = queries.find_version(conn, inst.id, from_id)
        to_v = queries.find_version(conn, inst.id, to_id)
        if from_v is None or to_v is None:
            abort(404)

        # Order them chronologically regardless of which one was
        # passed as ``from``/``to`` — diffs are stored newer→older but
        # rendering reads better if we treat the older one as the
        # baseline.
        if from_v.commit_date > to_v.commit_date:
            from_v, to_v = to_v, from_v

        diffs = queries.list_diffs(conn, from_v.id, to_v.id)

    return render_template(
        "compare.html",
        instrument=inst,
        from_version=from_v,
        to_version=to_v,
        diffs=diffs,
        all_versions=all_versions,
    )


# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------

@bp.get("/healthz")
def healthz():
    return {"status": "ok"}
