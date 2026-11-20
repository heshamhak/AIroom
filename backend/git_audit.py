from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

from executor import redact_secrets


def _hash_git_control_files(root: Path) -> dict[str, str]:
    """Hash git config/attribute files to detect filter-driver tampering."""
    targets = [
        root / ".git" / "config",
        root / ".gitattributes",
        root / ".git" / "info" / "attributes",
    ]
    hashes: dict[str, str] = {}
    for t in targets:
        if t.is_file():
            hashes[t.relative_to(root).as_posix()] = hashlib.sha256(t.read_bytes()).hexdigest()
    return hashes


def collect_tamper_resistant_diff(
    workspace: str | Path,
    expected_hashes: dict[str, str] | None = None,
    max_chars: int = 8000,
) -> dict:
    """Safely collect git diff --no-ext-diff --no-textconv after verifying git control files."""
    root = Path(workspace).expanduser().resolve()
    git_dir = root / ".git"
    if not git_dir.exists():
        raise ValueError("Workspace is not a git repository.")

    current_hashes = _hash_git_control_files(root)
    if expected_hashes is not None and current_hashes != expected_hashes:
        raise RuntimeError("Git control files (.git/config or .gitattributes) changed; failing closed.")

    git_cfg = root / ".git" / "config"
    if git_cfg.is_file():
        cfg_text = git_cfg.read_text(encoding="utf-8", errors="ignore").lower()
        if "[filter " in cfg_text:
            raise RuntimeError("Custom git filter driver detected in .git/config; failing closed.")

    proc = subprocess.run(
        ["git", "-c", "core.fsmonitor=false", "diff", "--no-ext-diff", "--no-textconv"],
        cwd=str(root),
        capture_output=True,
        text=True,
        timeout=15,
        shell=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git diff failed: {redact_secrets(proc.stderr)}")

    diff_text = redact_secrets(proc.stdout)
    truncated = len(diff_text) > max_chars
    if truncated:
        diff_text = diff_text[:max_chars] + "\n... (diff truncated)"

    return {
        "ok": True,
        "control_hashes": current_hashes,
        "truncated": truncated,
        "diff": diff_text,
    }
