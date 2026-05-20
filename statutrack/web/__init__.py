"""Flask app factory for StatuTrack."""
from __future__ import annotations

import os

from flask import Flask


def create_app(config: dict | None = None) -> Flask:
    app = Flask(
        __name__,
        template_folder="templates",
        static_folder="static",
    )
    app.config.update(
        SECRET_KEY=os.environ.get("STATUTRACK_SECRET_KEY", os.urandom(32)),
        # Default keeps the SQLite file OUT of the docroot — the app root
        # is internet-facing, so anything served as a static file
        # (.sqlite, .db, …) would be downloadable absent .htaccess.
        DATABASE_PATH=os.environ.get(
            "STATUTRACK_DB",
            os.path.expanduser("~/data/statutrack/statutrack.sqlite"),
        ),
        LAWS_XML_PATH=os.environ.get(
            "STATUTRACK_LAWS_XML",
            os.path.expanduser("~/data/laws-lois-xml"),
        ),
    )
    if config:
        app.config.update(config)

    from . import routes

    app.register_blueprint(routes.bp)
    return app
