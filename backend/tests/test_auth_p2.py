from __future__ import annotations

import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient
import pytest

import main as app_main
from auth import TokenAuth


def test_p2_token_auth_rest_ws_and_path_traversal(tmp_path: Path, monkeypatch):
    token_file = tmp_path / "api_token"
    monkeypatch.setattr(app_main.auth, "token_path", token_file)
    app_main.auth.reset_token(None)
    monkeypatch.setattr(app_main.store.db, "db_path", tmp_path / "p2.db")

    with TestClient(app_main.app) as client:
        # 1. Token file created with 0600 permissions
        assert token_file.exists()
        assert stat.S_IMODE(token_file.stat().st_mode) == 0o600
        tok = app_main.auth.token

        # 2. /health is public; /api/* requires token
        assert client.get("/health").status_code == 200
        assert client.get("/api/sessions").status_code == 401

        headers = {"Authorization": f"Bearer {tok}"}
        res = client.post(
            "/api/sessions",
            headers=headers,
            json={
                "title": "P2 Auth Test",
                "user_request": "Test auth",
                "target_workspace": str(tmp_path),
                "mode": "simulation",
            },
        )
        assert res.status_code == 200
        sid = res.json()["id"]

        # 3. Export & read artifact safely; block path traversal
        assert client.post(f"/api/sessions/{sid}/export", headers=headers).status_code == 200
        arts = client.get(f"/api/sessions/{sid}/artifacts", headers=headers).json()
        assert len(arts) >= 1

        ok_read = client.get(
            f"/api/sessions/{sid}/artifacts/read",
            headers=headers,
            params={"filename": arts[0]["name"]},
        )
        assert ok_read.status_code == 200

        trav = client.get(
            f"/api/sessions/{sid}/artifacts/read",
            headers=headers,
            params={"filename": "../../etc/passwd"},
        )
        assert trav.status_code == 403

        # 4. WebSocket rejects invalid token and accepts valid token
        with pytest.raises(Exception):
            with client.websocket_connect(f"/ws/sessions/{sid}?token=wrong"):
                pass

        with client.websocket_connect(f"/ws/sessions/{sid}?token={tok}&last_seq=0") as ws:
            msg = ws.receive_json()
            assert msg["type"] == "replay"
