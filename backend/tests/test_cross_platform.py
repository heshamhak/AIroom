from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from cdp_bridge import find_browser_executable
from config import load_settings
from runner import validate_verify_command


def test_cross_platform_browser_override_and_missing(tmp_path: Path, monkeypatch):
    fake_browser = tmp_path / "chrome.exe"
    fake_browser.write_text("bin", encoding="utf-8")
    monkeypatch.setenv("AIROOM_BROWSER_PATH", str(fake_browser))
    assert find_browser_executable() == str(fake_browser)


def test_cross_platform_windows_localappdata_paths(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("AIROOM_DATA_DIR", raising=False)
    monkeypatch.delenv("AIROOM_CHROME_DIR", raising=False)
    monkeypatch.setattr("config._is_windows", lambda: True)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "AppDataLocal"))

    cfg = load_settings()
    assert cfg.data_dir == (tmp_path / "AppDataLocal" / "airoom").resolve()
    assert cfg.chrome_dir == (tmp_path / "AppDataLocal" / "airoom-chrome").resolve()


def test_cross_platform_verify_command_stems():
    assert validate_verify_command("pytest.exe -q") == ["pytest.exe", "-q"]
    assert validate_verify_command("flutter.bat test") == ["flutter.bat", "test"]
    with pytest.raises(ValueError):
        validate_verify_command("powershell -Command whoami")
