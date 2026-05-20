"""Phase 0 smoke tests for the Flask wiring."""
from __future__ import annotations

from statutrack.web import create_app


def test_index_responds():
    client = create_app().test_client()
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"StatuTrack" in resp.data


def test_healthz():
    client = create_app().test_client()
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.get_json() == {"status": "ok"}
