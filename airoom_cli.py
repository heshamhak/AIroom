#!/usr/bin/env python3
"""Zero-study 1-command CLI for coding agents to get a ChatGPT+Claude approved plan via AIroom."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "backend"))

from config import load_settings  # noqa: E402


def _ensure_backend(base_url: str) -> subprocess.Popen | None:
    try:
        with urllib.request.urlopen(f"{base_url}/health", timeout=1.0) as r:
            if r.status == 200:
                return None
    except Exception:
        pass

    py = ROOT / "backend" / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    proc = subprocess.Popen(
        [str(py), "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", "8765"],
        cwd=str(ROOT / "backend"),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(30):
        try:
            with urllib.request.urlopen(f"{base_url}/health", timeout=1.0) as r:
                if r.status == 200:
                    return proc
        except Exception:
            time.sleep(0.25)
    proc.terminate()
    raise RuntimeError("AIroom backend failed to start.")


def _req(method: str, url: str, token: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="AIroom 1-command consensus planner for coding agents.")
    parser.add_argument("title", help="Project or task title")
    parser.add_argument("request", help="What needs to be planned and approved")
    parser.add_argument("--workspace", default=os.getcwd(), help="Target workspace path")
    parser.add_argument("--mode", default="live_cdp", choices=["live_cdp", "ollama_local", "simulation"])
    parser.add_argument("--rounds", type=int, default=4, help="Max negotiation rounds")
    parser.add_argument("--json", action="store_true", help="Output JSON result")
    args = parser.parse_args()

    cfg = load_settings()
    base_url = f"http://{cfg.host}:{cfg.port}"
    spawned = _ensure_backend(base_url)

    try:
        token = cfg.token_path.read_text(encoding="utf-8").strip()
        session = _req(
            "POST",
            f"{base_url}/api/sessions",
            token,
            {
                "title": args.title,
                "user_request": args.request,
                "target_workspace": str(Path(args.workspace).resolve()),
                "cdp_url": cfg.cdp_url,
                "mode": args.mode,
                "max_rounds": args.rounds,
            },
        )
        sid = session["id"]
        _req("POST", f"{base_url}/api/sessions/{sid}/start", token)

        while True:
            time.sleep(1.5)
            cur = _req("GET", f"{base_url}/api/sessions/{sid}", token)
            if cur["stage"] in ("consensus_reached", "exported", "verified_done", "failed", "paused"):
                break

        if args.json:
            print(json.dumps(cur, ensure_ascii=False, indent=2))
        else:
            print(f"STAGE: {cur['stage']} (session={sid})")
            if cur.get("error"):
                print(f"ERROR: {cur['error']}")
            for f in cur.get("exported_files", []):
                print(f"FILE: {f}")
            if cur.get("executor_prompt"):
                print("\n--- EXECUTOR PROMPT ---\n" + cur["executor_prompt"])
        return 0 if cur["stage"] in ("consensus_reached", "exported", "verified_done") else 1
    finally:
        if spawned is not None:
            spawned.terminate()


if __name__ == "__main__":
    raise SystemExit(main())
