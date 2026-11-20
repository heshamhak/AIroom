from __future__ import annotations

import hmac
import os
import secrets
from pathlib import Path

from fastapi import Depends, HTTPException, Header, Query, WebSocket, status


from config import load_settings

DEFAULT_TOKEN_PATH = load_settings().token_path


def load_or_create_api_token(token_path: Path = DEFAULT_TOKEN_PATH) -> str:
    """Load API token from env or disk; atomically create with 0600 perms if missing."""
    env_token = os.environ.get("AIROOM_API_TOKEN", "").strip()
    if env_token:
        return env_token

    token_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(token_path.parent, 0o700)
    except OSError:
        pass

    if token_path.exists():
        mode = token_path.stat().st_mode & 0o777
        if mode & 0o077:
            os.chmod(token_path, 0o600)
        existing = token_path.read_text(encoding="utf-8").strip()
        if existing:
            return existing

    token = secrets.token_urlsafe(32)
    fd = os.open(token_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, token.encode("utf-8"))
    finally:
        os.close(fd)
    return token


def verify_token_value(provided: str | None, expected: str) -> bool:
    if not provided:
        return False
    return hmac.compare_digest(provided.strip(), expected)


class TokenAuth:
    """Dependency & WebSocket verifier for AIroom API token."""

    def __init__(self, token_path: Path = DEFAULT_TOKEN_PATH) -> None:
        self.token_path = token_path
        self._token: str | None = None

    @property
    def token(self) -> str:
        if self._token is None:
            self._token = load_or_create_api_token(self.token_path)
        return self._token

    def reset_token(self, new_token: str | None = None) -> None:
        self._token = new_token

    def require_http(
        self,
        authorization: str | None = Header(default=None),
        x_api_token: str | None = Header(default=None),
    ) -> None:
        candidate = x_api_token
        if not candidate and authorization and authorization.lower().startswith("bearer "):
            candidate = authorization[7:].strip()
        if not verify_token_value(candidate, self.token):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or missing AIroom API token.",
            )

    def verify_ws(self, websocket: WebSocket, token_param: str | None) -> bool:
        candidate = token_param
        if not candidate:
            auth_hdr = websocket.headers.get("authorization", "")
            if auth_hdr.lower().startswith("bearer "):
                candidate = auth_hdr[7:].strip()
        return verify_token_value(candidate, self.token)
