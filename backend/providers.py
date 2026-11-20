from __future__ import annotations

import os
from typing import Protocol
import httpx


class ProviderAdapter(Protocol):
    name: str

    async def generate(self, prompt: str, new_chat: bool = False) -> str: ...


class OllamaAdapter:
    """Local Ollama adapter (http://127.0.0.1:11434) for offline planning/validation/tie-breaking."""

    def __init__(
        self,
        model: str | None = None,
        base_url: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = (
            base_url or os.environ.get("AIROOM_OLLAMA_URL", "http://127.0.0.1:11434")
        ).rstrip("/")
        self.model = model or os.environ.get("AIROOM_OLLAMA_MODEL", "qwen2.5-coder:7b")
        self.name = f"ollama:{self.model}"
        self._client = client

    async def list_models(self) -> list[str]:
        if self._client is not None:
            resp = await self._client.get(f"{self.base_url}/api/tags")
            resp.raise_for_status()
            return [m["name"] for m in resp.json().get("models", [])]

        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{self.base_url}/api/tags")
            resp.raise_for_status()
            return [m["name"] for m in resp.json().get("models", [])]

    async def generate(self, prompt: str, new_chat: bool = False) -> str:
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
        }
        if self._client is not None:
            resp = await self._client.post(f"{self.base_url}/api/chat", json=payload)
            resp.raise_for_status()
            return resp.json()["message"]["content"].strip()

        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(f"{self.base_url}/api/chat", json=payload)
            resp.raise_for_status()
            return resp.json()["message"]["content"].strip()
