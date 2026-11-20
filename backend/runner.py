from __future__ import annotations

import asyncio
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable

from executor import redact_secrets

# Allowlisted verification executables (no arbitrary shell commands)
ALLOWED_VERIFY_BINARIES = {
    "pytest",
    "python",
    "python3",
    "flutter",
    "npm",
    "node",
}
ALLOWED_RELATIVE_SCRIPTS = {
    "./scripts/validate_release.sh",
    "scripts/validate_release.sh",
}


@dataclass(frozen=True)
class VerifyResult:
    ok: bool
    exit_code: int
    command: str
    output: str


def validate_verify_command(command: str) -> list[str]:
    """Validate and split a verification command against an explicit safe allowlist."""
    raw = command.strip()
    if not raw:
        raise ValueError("Verification command must not be empty.")
    if any(bad in raw for bad in (";", "&&", "||", "|", "`", "$(", ">", "<")):
        raise ValueError("Shell chaining/redirection operators are not allowed in verify_command.")

    parts = shlex.split(raw)
    if not parts:
        raise ValueError("Invalid verification command.")

    exe = Path(parts[0]).name
    if parts[0] in ALLOWED_RELATIVE_SCRIPTS or exe in ALLOWED_VERIFY_BINARIES:
        return parts
    raise ValueError(f"Command '{parts[0]}' is not in the AIroom verification allowlist.")


async def run_verification(
    workspace: str,
    command: str,
    timeout_s: int = 120,
) -> VerifyResult:
    """Execute an allowlisted verification command inside the resolved workspace."""
    parts = validate_verify_command(command)
    ws_path = Path(workspace).expanduser().resolve()
    if not ws_path.is_dir():
        raise ValueError(f"Workspace does not exist: {ws_path}")

    def _exec() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            parts,
            cwd=str(ws_path),
            capture_output=True,
            text=True,
            timeout=timeout_s,
            shell=False,
        )

    proc = await asyncio.to_thread(_exec)
    combined = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
    safe_out = redact_secrets(combined.strip())
    if len(safe_out) > 4000:
        safe_out = safe_out[-4000:]

    return VerifyResult(
        ok=(proc.returncode == 0),
        exit_code=proc.returncode,
        command=command,
        output=safe_out,
    )


async def run_verification_streaming(
    workspace: str,
    command: str,
    on_line: Callable[[str, str], Awaitable[None]] | None = None,
    timeout_s: int = 120,
    max_capture_bytes: int = 16384,
) -> VerifyResult:
    """Stream stdout/stderr line-by-line while always draining pipes to avoid deadlocks."""
    parts = validate_verify_command(command)
    ws_path = Path(workspace).expanduser().resolve()
    if not ws_path.is_dir():
        raise ValueError(f"Workspace does not exist: {ws_path}")

    proc = await asyncio.create_subprocess_exec(
        *parts,
        cwd=str(ws_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    captured: list[str] = []
    captured_bytes = 0

    async def _drain(stream: asyncio.StreamReader | None, stream_name: str) -> None:
        nonlocal captured_bytes
        if stream is None:
            return
        while True:
            raw_line = await stream.readline()
            if not raw_line:
                break
            line = redact_secrets(raw_line.decode("utf-8", errors="replace").rstrip("\r\n"))
            if captured_bytes < max_capture_bytes:
                captured.append(line)
                captured_bytes += len(line.encode("utf-8"))
            if on_line is not None:
                try:
                    await on_line(stream_name, line)
                except Exception:
                    pass

    try:
        await asyncio.wait_for(
            asyncio.gather(_drain(proc.stdout, "stdout"), _drain(proc.stderr, "stderr"), proc.wait()),
            timeout=timeout_s,
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise TimeoutError(f"Verification command timed out after {timeout_s}s.")

    return VerifyResult(
        ok=(proc.returncode == 0),
        exit_code=proc.returncode or 0,
        command=command,
        output="\n".join(captured),
    )


def build_self_heal_prompt(
    title: str,
    verify_cmd: str,
    error_output: str,
    heal_round: int,
) -> str:
    """Build a compact English self-healing prompt when verification tests fail."""
    return (
        f"Closed-Loop Verification Failed (Heal Round {heal_round}) for '{title}'.\n"
        f"Command: {verify_cmd}\n"
        f"Output:\n```\n{error_output[-2000:]}\n```\n"
        f"Propose a targeted fix in [PLAN_ITEM]...[AGREE_REQUEST]...[/PLAN_ITEM] format so tests pass."
    )
