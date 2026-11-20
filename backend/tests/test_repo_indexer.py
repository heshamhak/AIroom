from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

import main as app_main
from repo_indexer import build_repo_map


def test_repo_map_indexes_symbols_and_excludes_secrets(tmp_path: Path):
    (tmp_path / "app.py").write_text("class Engine:\n    pass\n\ndef run_task():\n    return 1\n", encoding="utf-8")
    (tmp_path / "api_token").write_text("SECRET_TOKEN_VALUE", encoding="utf-8")
    (tmp_path / ".env").write_text("SECRET_KEY=123", encoding="utf-8")
    (tmp_path / "airoom.db").write_bytes(b"sqlite")
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "ignored.py").write_text("def hidden(): pass\n", encoding="utf-8")

    rmap = build_repo_map(tmp_path)
    assert "app.py [Engine, run_task]" in rmap
    assert "api_token" not in rmap
    assert ".env" not in rmap
    assert "airoom.db" not in rmap
    assert "ignored.py" not in rmap


def test_repo_map_endpoint_requires_auth(tmp_path: Path, monkeypatch):
    token_file = tmp_path / "api_token"
    monkeypatch.setattr(app_main.auth, "token_path", token_file)
    app_main.auth.reset_token(None)
    monkeypatch.setattr(app_main.store.db, "db_path", tmp_path / "rmap.db")

    (tmp_path / "sample.py").write_text("def hello():\n    return 'ok'\n", encoding="utf-8")

    with TestClient(app_main.app) as client:
        assert client.get("/api/workspace/repo-map").status_code == 401
        res = client.get(
            "/api/workspace/repo-map",
            headers={"Authorization": f"Bearer {app_main.auth.token}"},
            params={"path": str(tmp_path)},
        )
        assert res.status_code == 200
        assert "sample.py [hello]" in res.json()["repo_map"]
