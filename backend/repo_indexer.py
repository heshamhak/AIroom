from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

IGNORED_DIRS = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    "build",
    ".dart_tool",
    "node_modules",
    ".airoom",
    "data",
}

SENSITIVE_FILES = {
    "api_token",
    ".env",
}

SYMBOL_RE = re.compile(r"^\s*(?:async\s+def|def|class)\s+([A-Za-z_][A-Za-z0-9_]*)", re.MULTILINE)


def build_repo_map(workspace: str | Path, max_files: int = 40, max_chars: int = 3500) -> str:
    """Build a compact, secret-free Repo Map (files, Python/Dart symbols, and git diff stat)."""
    root = Path(workspace).expanduser().resolve()
    if not root.is_dir():
        return f"Workspace not found: {root}"

    lines: list[str] = [f"RepoMap ({root.name}):"]
    file_count = 0

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in IGNORED_DIRS and not d.startswith("."))
        for fname in sorted(filenames):
            if fname in SENSITIVE_FILES or fname.endswith((".db", ".db-wal", ".db-shm", ".token", ".key", ".pem")):
                continue
            fpath = Path(dirpath) / fname
            rel = fpath.relative_to(root).as_posix()
            symbols: list[str] = []
            if fpath.suffix in (".py", ".dart") and fpath.stat().st_size < 100_000:
                try:
                    content = fpath.read_text(encoding="utf-8", errors="ignore")
                    symbols = SYMBOL_RE.findall(content)[:8]
                except OSError:
                    pass
            sym_str = f" [{', '.join(symbols)}]" if symbols else ""
            lines.append(f"- {rel}{sym_str}")
            file_count += 1
            if file_count >= max_files:
                lines.append("... (truncated)")
                break
        if file_count >= max_files:
            break

    # Append git diff --stat if inside a git repository
    try:
        proc = subprocess.run(
            ["git", "diff", "--stat"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=5,
            shell=False,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            lines.append("GitDiff:")
            lines.append(proc.stdout.strip())
    except Exception:
        pass

    out = "\n".join(lines)
    if len(out) > max_chars:
        out = out[:max_chars] + "\n..."
    return out
