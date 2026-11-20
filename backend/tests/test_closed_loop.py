from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient
import pytest

import main as app_main
from runner import build_self_heal_prompt, run_verification, validate_verify_command


def test_closed_loop_allowlist_blocks_shell_injection():
    assert validate_verify_command("pytest -q tests/") == ["pytest", "-q", "tests/"]
    assert validate_verify_command("./scripts/validate_release.sh") == ["./scripts/validate_release.sh"]

    with pytest.raises(ValueError, match="not allowed"):
        validate_verify_command("pytest && rm -rf /")

    with pytest.raises(ValueError, match="allowlist"):
        validate_verify_command("bash -c 'echo hi'")


def test_closed_loop_run_verification_pass_and_fail(tmp_path: Path):
    fail_script = tmp_path / "fail_test.py"
    fail_script.write_text("raise SystemExit(2)\n", encoding="utf-8")

    async def _run():
        ok_res = await run_verification(str(tmp_path), "python3 -c 'print(123)'")
        assert ok_res.ok is True
        assert ok_res.exit_code == 0
        assert "123" in ok_res.output

        fail_res = await run_verification(str(tmp_path), f"python3 {fail_script.name}")
        assert fail_res.ok is False
        assert fail_res.exit_code == 2

        prompt = build_self_heal_prompt("Demo", fail_res.command, "AssertionError", 1)
        assert "Heal Round 1" in prompt
        assert "AssertionError" in prompt

    asyncio.run(_run())


def test_closed_loop_verify_endpoint_gating(tmp_path: Path, monkeypatch):
    token_file = tmp_path / "api_token"
    monkeypatch.setattr(app_main.auth, "token_path", token_file)
    app_main.auth.reset_token(None)
    monkeypatch.setattr(app_main.store.db, "db_path", tmp_path / "loop.db")

    with TestClient(app_main.app) as client:
        headers = {"Authorization": f"Bearer {app_main.auth.token}"}
        s = client.post(
            "/api/sessions",
            headers=headers,
            json={
                "title": "Closed Loop Gate",
                "user_request": "Test verify loop",
                "target_workspace": str(tmp_path),
                "mode": "simulation",
            },
        ).json()
        sid = s["id"]

        # Blocked before consensus
        r_blocked = client.post(
            f"/api/sessions/{sid}/verify-loop",
            headers=headers,
            json={"verify_command": "python3 -c 'print(1)'"},
        )
        assert r_blocked.status_code == 409

        # Export -> verify-loop succeeds -> verified_done
        assert client.post(f"/api/sessions/{sid}/export", headers=headers).status_code == 200
        r_ok = client.post(
            f"/api/sessions/{sid}/verify-loop",
            headers=headers,
            json={"verify_command": "python3 -c 'print(42)'"},
        )
        assert r_ok.status_code == 200
        assert r_ok.json()["ok"] is True
        assert r_ok.json()["stage"] == "verified_done"
