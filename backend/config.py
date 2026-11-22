from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("airoom.config")

APP_VERSION = "0.1.0"
DEFAULT_DEV_ORIGINS = [
    "http://localhost",
    "http://127.0.0.1",
]
DEFAULT_ORIGIN_REGEX = r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$"


def _is_windows() -> bool:
    return sys.platform.startswith("win")


def _default_data_dir() -> Path:
    if _is_windows():
        local_app = os.environ.get("LOCALAPPDATA", "").strip()
        if local_app:
            return Path(local_app) / "airoom"
    xdg = os.environ.get("XDG_DATA_HOME", "").strip()
    if xdg:
        return Path(xdg) / "airoom"
    local_data = Path(__file__).resolve().parent / "data"
    if local_data.exists():
        return local_data
    return Path.home() / ".local" / "share" / "airoom"


def _default_chrome_dir() -> Path:
    if _is_windows():
        local_app = os.environ.get("LOCALAPPDATA", "").strip()
        if local_app:
            return Path(local_app) / "airoom-chrome"
    return Path.home() / ".config" / "airoom-chrome"


def _parse_cors_origins(raw: str | None) -> list[str]:
    if not raw or not raw.strip():
        return list(DEFAULT_DEV_ORIGINS)
    origins = [o.strip() for o in raw.split(",") if o.strip()]
    if "*" in origins:
        raise ValueError("AIROOM_CORS_ORIGINS must not contain wildcard '*'.")
    return origins


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    db_path: Path
    token_path: Path
    chrome_dir: Path
    default_workspace: Path
    host: str
    port: int
    cdp_url: str
    cors_origins: list[str]
    log_level: str


def load_settings() -> Settings:
    data_dir = Path(os.environ.get("AIROOM_DATA_DIR", "").strip() or _default_data_dir()).expanduser().resolve()
    db_path = Path(os.environ.get("AIROOM_DB_PATH", "").strip() or (data_dir / "airoom.db")).expanduser().resolve()
    token_path = Path(os.environ.get("AIROOM_TOKEN_PATH", "").strip() or (data_dir / "api_token")).expanduser().resolve()
    chrome_dir = Path(
        os.environ.get("AIROOM_CHROME_DIR", "").strip() or _default_chrome_dir()
    ).expanduser().resolve()
    default_workspace = Path(
        os.environ.get("AIROOM_DEFAULT_WORKSPACE", "").strip() or Path.cwd()
    ).expanduser().resolve()

    host = os.environ.get("AIROOM_HOST", "127.0.0.1").strip() or "127.0.0.1"
    if host not in ("127.0.0.1", "localhost", "::1"):
        logger.warning("AIROOM_HOST is set to non-loopback address (%s). Ensure API token is protected.", host)

    port_raw = os.environ.get("AIROOM_PORT", "8765").strip() or "8765"
    port = int(port_raw)
    if not (1 <= port <= 65535):
        raise ValueError("AIROOM_PORT must be between 1 and 65535.")

    cdp_url = os.environ.get("AIROOM_CDP_URL", "http://127.0.0.1:9223").strip() or "http://127.0.0.1:9223"
    cors_origins = _parse_cors_origins(os.environ.get("AIROOM_CORS_ORIGINS"))
    log_level = os.environ.get("AIROOM_LOG_LEVEL", "INFO").strip().upper() or "INFO"

    return Settings(
        data_dir=data_dir,
        db_path=db_path,
        token_path=token_path,
        chrome_dir=chrome_dir,
        default_workspace=default_workspace,
        host=host,
        port=port,
        cdp_url=cdp_url,
        cors_origins=cors_origins,
        log_level=log_level,
    )
