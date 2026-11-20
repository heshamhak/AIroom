from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

import main as app_main
from templates_store import list_templates


def test_builtin_templates_and_endpoint(tmp_path: Path, monkeypatch):
    templates = list_templates()
    assert len(templates) >= 4
    assert all("verify_command" in t and "prompt" in t for t in templates)

    token_file = tmp_path / "api_token"
    monkeypatch.setattr(app_main.auth, "token_path", token_file)
    app_main.auth.reset_token(None)
    monkeypatch.setattr(app_main.store.db, "db_path", tmp_path / "tpl.db")

    with TestClient(app_main.app) as client:
        assert client.get("/api/templates").status_code == 401
        res = client.get(
            "/api/templates",
            headers={"Authorization": f"Bearer {app_main.auth.token}"},
        )
        assert res.status_code == 200
        assert len(res.json()) == len(templates)
