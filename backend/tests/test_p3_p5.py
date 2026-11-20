from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

import main as app_main
from executor import redact_secrets
from models import CdpStatusResponse


def test_p3_secret_redaction():
    raw = "Authorization: Bearer super_secret_12345 token=my_pass sk-abcdefghijklmnop"
    clean = redact_secrets(raw)
    assert "super_secret_12345" not in clean
    assert "my_pass" not in clean
    assert "sk-abcdefghijklmnop" not in clean


def test_p3_p5_execution_gating_history_and_health_details(tmp_path: Path, monkeypatch):
    token_file = tmp_path / "api_token"
    monkeypatch.setattr(app_main.auth, "token_path", token_file)
    app_main.auth.reset_token(None)
    monkeypatch.setattr(app_main.store.db, "db_path", tmp_path / "p3_p5.db")

    async def _mock_cdp(url):
        return CdpStatusResponse(connected=False, cdp_url=url)

    monkeypatch.setattr(app_main, "inspect_cdp", _mock_cdp)

    with TestClient(app_main.app) as client:
        headers = {"Authorization": f"Bearer {app_main.auth.token}"}

        # Health details
        hd = client.get("/api/health/details", headers=headers)
        assert hd.status_code == 200
        body = hd.json()
        assert body["sqlite"]["ok"] is True
        assert body["disk"]["total_mb"] > 0

        # Create session (idle) -> execute must be blocked (409)
        s = client.post(
            "/api/sessions",
            headers=headers,
            json={
                "title": "P3 Gate",
                "user_request": "Test gating",
                "target_workspace": str(tmp_path),
                "mode": "simulation",
            },
        ).json()
        sid = s["id"]

        blocked = client.post(f"/api/sessions/{sid}/execute", headers=headers)
        assert blocked.status_code == 409

        # Export -> stage becomes exported -> execute succeeds & records history
        assert client.post(f"/api/sessions/{sid}/export", headers=headers).status_code == 200
        ok_exec = client.post(f"/api/sessions/{sid}/execute", headers=headers)
        assert ok_exec.status_code == 200
        assert ok_exec.json()["status"] == "ready_for_executor"

        hist = client.get(f"/api/sessions/{sid}/executions", headers=headers).json()
        assert len(hist) == 1
        assert hist[0]["status"] == "ready_for_executor"
