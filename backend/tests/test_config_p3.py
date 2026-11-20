from __future__ import annotations

import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from auth import load_or_create_api_token
from config import load_settings


def test_p3_config_defaults_and_env_override(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AIROOM_DATA_DIR", str(tmp_path / "custom_data"))
    monkeypatch.setenv("AIROOM_HOST", "127.0.0.1")
    monkeypatch.setenv("AIROOM_PORT", "9001")
    monkeypatch.setenv("AIROOM_CDP_URL", "http://127.0.0.1:9333")
    monkeypatch.setenv("AIROOM_CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:8080")

    cfg = load_settings()
    assert cfg.data_dir == (tmp_path / "custom_data").resolve()
    assert cfg.db_path == (tmp_path / "custom_data" / "airoom.db").resolve()
    assert cfg.token_path == (tmp_path / "custom_data" / "api_token").resolve()
    assert cfg.port == 9001
    assert cfg.cdp_url == "http://127.0.0.1:9333"
    assert cfg.cors_origins == ["http://localhost:3000", "http://127.0.0.1:8080"]


def test_p3_cors_rejects_wildcard_and_invalid_port(monkeypatch):
    monkeypatch.setenv("AIROOM_CORS_ORIGINS", "*")
    with pytest.raises(ValueError, match="wildcard"):
        load_settings()

    monkeypatch.delenv("AIROOM_CORS_ORIGINS", raising=False)
    monkeypatch.setenv("AIROOM_PORT", "99999")
    with pytest.raises(ValueError, match="AIROOM_PORT"):
        load_settings()


def test_p3_atomic_token_creation_0600(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("AIROOM_API_TOKEN", raising=False)
    tok_path = tmp_path / "sec" / "api_token"
    tok = load_or_create_api_token(tok_path)
    assert len(tok) >= 32
    assert stat.S_IMODE(tok_path.stat().st_mode) == 0o600
    assert load_or_create_api_token(tok_path) == tok
