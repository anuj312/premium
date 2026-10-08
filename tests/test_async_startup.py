"""Kite stalls must never stall the Render web server."""
import asyncio
import threading
from types import SimpleNamespace
from fastapi import HTTPException
from fastapi.testclient import TestClient

import main


def test_website_and_health_respond_before_kite_seeding_finishes(monkeypatch):
    entered = threading.Event()
    release = threading.Event()

    def delayed_live_engine():
        entered.set()
        if not release.wait(timeout=8):
            raise TimeoutError("Test setup timed out")
        return main.MarketEngine(main.DemoProvider(), "demo")

    monkeypatch.setattr(main, "build_engine", delayed_live_engine)
    monkeypatch.setattr(main, "MODE", "live")
    monkeypatch.setattr(main, "POLL_SECONDS", 1)
    try:
        with TestClient(main.app) as client:
            assert entered.wait(timeout=3), "Background seed did not start"
            # Neither should wait for delayed build_engine().
            response = client.get("/")
            assert response.status_code == 200 and "seedOverlay" in response.text
            health = client.get("/healthz")
            assert health.status_code == 200
            assert health.json()["market_status"] == "starting"
            state = client.get("/api/state").json()
            assert state["status"] == "starting"
            assert state["meta"]["seed_stage"] == "LOADING NSE & NFO INSTRUMENTS"
            assert state["stocks"] == []
            # Chart requests get an explicit pending response, not a server error.
            chart = client.get("/api/chart/MPHASIS")
            assert chart.status_code == 503
            release.set()
            for _ in range(30):
                data = client.get("/api/state").json()
                if data["status"] == "ok":
                    break
                import time
                time.sleep(.08)
            assert data["status"] == "ok"
            assert data["stocks"]
    finally:
        release.set()


def test_bad_credentials_leave_website_online(monkeypatch):
    monkeypatch.setattr(main, "MODE", "live")
    monkeypatch.setattr(main, "POLL_SECONDS", 1)

    def broken():
        raise RuntimeError("secret-example-should-not-leak")

    monkeypatch.setattr(main, "build_engine", broken)
    with TestClient(main.app) as client:
        for _ in range(30):
            data = client.get("/api/state").json()
            if data["status"] == "error":
                break
            import time
            time.sleep(.05)
        assert data["status"] == "error"
        assert "secret-example-should-not-leak" not in str(data)
        assert client.get("/").status_code == 200
        assert client.get("/healthz").json()["status"] == "online"
