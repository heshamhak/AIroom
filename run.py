#!/usr/bin/env python3
"""Unified cross-platform launcher for AIroom (Windows & Linux)."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKEND_DIR = ROOT / "backend"
APP_DIR = ROOT / "app"
VENV_DIR = BACKEND_DIR / ".venv"


def _venv_python() -> Path:
    if os.name == "nt":
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def ensure_backend_venv() -> Path:
    py = _venv_python()
    if not py.exists():
        print("[AIroom] Creating Python virtual environment...")
        subprocess.run([sys.executable, "-m", "venv", str(VENV_DIR)], check=True)
        subprocess.run(
            [str(py), "-m", "pip", "install", "-r", str(BACKEND_DIR / "requirements.txt")],
            check=True,
        )
    return py


def wait_for_health(url: str = "http://127.0.0.1:8765/health", timeout_s: int = 20) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.0) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            time.sleep(0.3)
    return False


def main() -> int:
    py = ensure_backend_venv()
    host = os.environ.get("AIROOM_HOST", "127.0.0.1")
    port = os.environ.get("AIROOM_PORT", "8765")

    print(f"[AIroom] Starting backend on http://{host}:{port} ...")
    backend_proc = subprocess.Popen(
        [str(py), "-m", "uvicorn", "main:app", "--host", host, "--port", port],
        cwd=str(BACKEND_DIR),
    )

    try:
        if not wait_for_health(f"http://{host}:{port}/health"):
            print("[AIroom] Backend failed to become healthy.")
            return 1

        flutter_bin = shutil.which("flutter.bat" if os.name == "nt" else "flutter") or shutil.which("flutter")
        if not flutter_bin:
            print("[AIroom] Flutter CLI not found in PATH. Backend is running; press Ctrl+C to stop.")
            backend_proc.wait()
            return 0

        target = "windows" if os.name == "nt" else "linux"
        if "--web" in sys.argv:
            target = "chrome"

        print(f"[AIroom] Launching Flutter app (-d {target}) ...")
        app_proc = subprocess.run([flutter_bin, "run", "-d", target], cwd=str(APP_DIR))
        return app_proc.returncode
    except KeyboardInterrupt:
        return 0
    finally:
        backend_proc.terminate()
        try:
            backend_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            backend_proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
