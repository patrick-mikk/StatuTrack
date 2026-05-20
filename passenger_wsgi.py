"""Passenger WSGI entry point for StatuTrack.

cPanel's Setup Python App wrote a generic boilerplate here on
application creation. That template loads ``passenger_wsgi.py`` as a
module named ``wsgi`` — a self-reference that infinite-loops on
import. cPanel only rewrites this file when you click Create or
Recreate in the Setup Python App UI; routine deploys and Passenger
restarts leave it alone. If you ever do recreate the app, run
``git checkout passenger_wsgi.py`` to put this back.

Apache/Passenger pulls the virtualenv path from ``PassengerPython`` in
the .htaccess block cPanel manages, so we don't re-exec the
interpreter from here.
"""
from __future__ import annotations

import os
import sys

APP_ROOT = os.path.dirname(os.path.abspath(__file__))
if APP_ROOT not in sys.path:
    sys.path.insert(0, APP_ROOT)

from statutrack.web import create_app  # noqa: E402

application = create_app()
