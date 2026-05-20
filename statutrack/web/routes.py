"""HTTP routes for StatuTrack.

Phase 0 only ships a landing page that confirms the Passenger -> Flask
wiring works. Reader/diff/search routes come online in Phases 3-5.
"""
from __future__ import annotations

from flask import Blueprint, current_app, render_template

bp = Blueprint("statutrack", __name__)


@bp.get("/")
def index():
    return render_template(
        "index.html",
        db_path=current_app.config["DATABASE_PATH"],
        laws_xml_path=current_app.config["LAWS_XML_PATH"],
    )


@bp.get("/healthz")
def healthz():
    return {"status": "ok"}
