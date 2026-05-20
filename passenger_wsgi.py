"""Passenger WSGI entry point for StatuTrack.

cPanel's Setup Python App writes a Passenger config block into the
parent directory's .htaccess pointing at the virtualenv's Python
interpreter, so we don't re-exec here. We just put the app root on
sys.path and expose the Flask app as ``application``.
"""
from __future__ import annotations

import os
import sys

APP_ROOT = os.path.dirname(os.path.abspath(__file__))
if APP_ROOT not in sys.path:
    sys.path.insert(0, APP_ROOT)

from statutrack.web import create_app  # noqa: E402

application = create_app()
