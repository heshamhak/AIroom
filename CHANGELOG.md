# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-11-20

### Initial release
- **Orchestrator Backend (`backend/`)**: FastAPI + WebSocket server driving ChatGPT (Planner) and Claude (Validator) via Chrome DevTools Protocol (`cdp_bridge.py`).
- **Consensus Protocol (`protocol.py`)**: Structured `[PLAN_ITEM]`, `[REVIEW_ITEM]`, `[STATUS: ...]`, and `[CONSENSUS_FINAL: ALL_APPROVED]` parsing, plus `compact_relay_text()` preserving fenced code blocks.
- **Persistent SQLite Storage (`db.py`)**: WAL mode, `aiosqlite`, single-writer queue, `schema_version` migrations, `VACUUM INTO` backups with isolated restore verification, `last_seq` WebSocket replay, context compaction, and whitelisted external-chat cleanup marking.
- **Security (`auth.py`, `config.py`, `executor.py`)**: Atomic `0600` bearer API token, restricted CORS, path-traversal-safe `.airoom/` artifact reader, secret redaction, and consensus-gated execution handoff.
- **Cross-Platform Flutter App (`app/`)**: Light translucent UI with RTL Arabic support, automatic desktop token loading, WebSocket reconnection with exponential backoff, and Linux/Web/Android support.
