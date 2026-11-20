from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
from fastapi.testclient import TestClient

import main as app_main
from providers import OllamaAdapter


def test_ollama_adapter_list_and_generate_hermetic():
    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "qwen2.5-coder:7b"}]})
        if request.url.path == "/api/chat":
            return httpx.Response(
                200,
                json={"message": {"role": "assistant", "content": "[STATUS: APPROVED]"}},
            )
        return httpx.Response(404)

    async def _run():
        transport = httpx.MockTransport(_handler)
        async with httpx.AsyncClient(transport=transport) as client:
            adapter = OllamaAdapter(model="qwen2.5-coder:7b", client=client)
            models = await adapter.list_models()
            assert models == ["qwen2.5-coder:7b"]
            reply = await adapter.generate("Audit P1")
            assert "[STATUS: APPROVED]" in reply

    asyncio.run(_run())


def test_providers_ollama_endpoint_requires_auth(tmp_path: Path, monkeypatch):
    token_file = tmp_path / "api_token"
    monkeypatch.setattr(app_main.auth, "token_path", token_file)
    app_main.auth.reset_token(None)
    monkeypatch.setattr(app_main.store.db, "db_path", tmp_path / "prov.db")

    with TestClient(app_main.app) as client:
        assert client.get("/api/providers/ollama/models").status_code == 401
        res = client.get(
            "/api/providers/ollama/models",
            headers={"Authorization": f"Bearer {app_main.auth.token}"},
        )
        assert res.status_code == 200
        assert "available" in res.json()
