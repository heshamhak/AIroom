# AIroom — Quick Agent Instructions

Coding agents do **not** need to inspect the repository internals to use AIroom.

## One-Command Consensus Planning

```bash
python3 airoom_cli.py "Project Title" "Requirements..." --workspace /path/to/project
```
(Or `airoom-ask "Project Title" "Requirements..."` if installed in PATH.)

- Modes: `--mode live_cdp` (default: ChatGPT + Claude via Chrome CDP), `--mode ollama_local`, `--mode simulation`.
- Approved plans are written to `<workspace>/.airoom/<session_id>_approved_plan.md` and `<session_id>_executor_prompt.txt`.
- Launch GUI: `python run.py` (`./start_airoom.sh` on Linux, `start_airoom.bat` on Windows).
- Validate repo: `./scripts/validate_release.sh`.
