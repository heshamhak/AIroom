from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
import pytest

from git_audit import collect_tamper_resistant_diff
from providers import OllamaAdapter
from runner import run_verification_streaming
from tiebreaker import TieBreakKey, run_hybrid_tiebreaker


def test_v020_tiebreaker_key_includes_target_provider_and_arbitrates():
    k1 = TieBreakKey("s1", 2, "quota", "chatgpt").as_str()
    k2 = TieBreakKey("s1", 2, "quota", "claude").as_str()
    assert k1 != k2

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"message": {"role": "assistant", "content": '[PLAN_ITEM id="P1" title="Fixed"]ok[/PLAN_ITEM]'}},
        )

    async def _run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(_handler)) as client:
            adapter = OllamaAdapter(model="qwen2.5-coder:7b", client=client)
            key, reply = await run_hybrid_tiebreaker("s1", 2, "quota", "chatgpt", "P1", "fix", adapter=adapter)
            assert "chatgpt" in key
            assert "[PLAN_ITEM" in reply

    asyncio.run(_run())


def test_v020_streaming_runner_drains_large_output_without_deadlock(tmp_path: Path):
    # Child writes >200KB (far exceeding 64KB pipe buffer and 4KB capture cap)
    chatty = tmp_path / "chatty.py"
    chatty.write_text(
        "for i in range(4000):\n    print(f'line-{i}-' + 'X' * 50)\n",
        encoding="utf-8",
    )

    streamed: list[str] = []

    async def _on_line(stream: str, line: str) -> None:
        streamed.append(line)

    async def _run():
        res = await run_verification_streaming(
            str(tmp_path),
            f"python3 {chatty.name}",
            on_line=_on_line,
            timeout_s=10,
            max_capture_bytes=4096,
        )
        assert res.ok is True
        assert res.exit_code == 0
        assert len(streamed) == 4000
        assert len(res.output.encode("utf-8")) <= 4200

    asyncio.run(_run())


def test_v020_tamper_resistant_git_diff_blocks_filter_driver(tmp_path: Path):
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=tmp_path, check=True)
    f = tmp_path / "demo.py"
    f.write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "demo.py"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True, capture_output=True)

    f.write_text("x = 2\n", encoding="utf-8")
    res = collect_tamper_resistant_diff(tmp_path)
    assert res["ok"] is True
    assert "+x = 2" in res["diff"]

    # Inject a suspicious filter driver in .git/config -> must fail closed
    cfg = tmp_path / ".git" / "config"
    cfg.write_text(cfg.read_text() + '\n[filter "evil"]\n\tclean = echo pwn\n', encoding="utf-8")
    with pytest.raises(RuntimeError, match="filter driver"):
        collect_tamper_resistant_diff(tmp_path)
