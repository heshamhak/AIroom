from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, TypeVar

import aiosqlite

from config import load_settings
from models import MarkerStatus, PlanItem, RelayMessage, Session, SessionMode, SessionStage

SUPPORTED_SCHEMA_VERSION = 1

T = TypeVar("T")

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL,
    migration_id TEXT NOT NULL,
    checksum TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    user_request TEXT NOT NULL,
    target_workspace TEXT NOT NULL,
    cdp_url TEXT NOT NULL,
    mode TEXT NOT NULL,
    max_rounds INTEGER NOT NULL,
    current_round INTEGER NOT NULL,
    stage TEXT NOT NULL,
    resume_step TEXT NOT NULL,
    latest_plan_text TEXT NOT NULL,
    final_plan_markdown TEXT NOT NULL,
    executor_prompt TEXT NOT NULL,
    exported_files_json TEXT NOT NULL,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    round_num INTEGER NOT NULL,
    sender TEXT NOT NULL,
    recipient TEXT NOT NULL,
    content TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    UNIQUE(session_id, message_id),
    UNIQUE(session_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_messages_session_seq ON messages(session_id, seq);

CREATE TABLE IF NOT EXISTS plan_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    round_num INTEGER NOT NULL,
    items_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS approvals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    round_num INTEGER NOT NULL,
    item_id TEXT NOT NULL,
    status TEXT NOT NULL,
    revision_note TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS execution_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    status TEXT NOT NULL,
    details TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS session_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    details_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS context_summaries (
    summary_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    from_seq INTEGER NOT NULL,
    to_seq INTEGER NOT NULL,
    summary_content TEXT NOT NULL,
    decisions_json TEXT NOT NULL,
    constraints_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS external_chats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    conversation_id TEXT,
    conversation_url TEXT NOT NULL,
    title TEXT,
    created_by_airoom INTEGER NOT NULL DEFAULT 0,
    deletion_status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL
);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class DatabaseManager:
    """Async SQLite manager with WAL mode, single-writer queue, migrations, and safe backups."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = Path(db_path) if db_path is not None else load_settings().db_path
        self._write_queue: asyncio.Queue[
            tuple[Callable[[aiosqlite.Connection], Awaitable[Any]], asyncio.Future[Any]]
        ] = asyncio.Queue()
        self._writer_task: asyncio.Task[Any] | None = None
        self._initialized = False

    async def init(self) -> None:
        if self._initialized:
            return
        self._write_queue = asyncio.Queue()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.db_path.parent, 0o700)
        except OSError:
            pass

        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute("PRAGMA journal_mode=WAL;")
            await conn.execute("PRAGMA busy_timeout=5000;")
            await conn.execute("PRAGMA foreign_keys=ON;")

            # Check existing schema_version before applying migrations
            cur = await conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version';"
            )
            has_ver_table = await cur.fetchone() is not None
            current_ver = 0
            if has_ver_table:
                cur = await conn.execute("SELECT COALESCE(MAX(version), 0) FROM schema_version;")
                row = await cur.fetchone()
                current_ver = int(row[0]) if row else 0

            if current_ver > SUPPORTED_SCHEMA_VERSION:
                raise RuntimeError(
                    f"Database schema version {current_ver} is newer than supported {SUPPORTED_SCHEMA_VERSION}."
                )

            if current_ver < SUPPORTED_SCHEMA_VERSION:
                if current_ver > 0 and self.db_path.exists():
                    await self.backup_database(self.db_path.parent / "backups", tag="pre_migration")
                await conn.execute("BEGIN IMMEDIATE;")
                try:
                    for stmt in [s.strip() for s in _SCHEMA_SQL.split(";") if s.strip()]:
                        await conn.execute(stmt + ";")
                    checksum = hashlib.sha256(_SCHEMA_SQL.encode("utf-8")).hexdigest()
                    await conn.execute(
                        """
                        INSERT OR IGNORE INTO schema_version (version, applied_at, migration_id, checksum)
                        VALUES (?, ?, ?, ?);
                        """,
                        (SUPPORTED_SCHEMA_VERSION, _now_iso(), "001_initial_p1", checksum),
                    )
                    await conn.commit()
                except Exception:
                    await conn.rollback()
                    raise

        try:
            os.chmod(self.db_path, 0o600)
        except OSError:
            pass

        self._writer_task = asyncio.create_task(self._writer_loop())
        self._initialized = True

    async def close(self) -> None:
        if self._initialized and not self._write_queue.empty():
            await self._write_queue.join()
        if self._writer_task and not self._writer_task.done():
            self._writer_task.cancel()
            try:
                await self._writer_task
            except asyncio.CancelledError:
                pass
        self._initialized = False

    async def _writer_loop(self) -> None:
        """Single-writer serialized queue for all short SQLite write transactions."""
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute("PRAGMA journal_mode=WAL;")
            await conn.execute("PRAGMA busy_timeout=5000;")
            await conn.execute("PRAGMA foreign_keys=ON;")
            while True:
                fn, fut = await self._write_queue.get()
                try:
                    result = await fn(conn)
                    await conn.commit()
                    if not fut.done():
                        fut.set_result(result)
                except Exception as exc:
                    try:
                        await conn.rollback()
                    except Exception:
                        pass
                    if not fut.done():
                        fut.set_exception(exc)
                finally:
                    self._write_queue.task_done()

    async def write(self, fn: Callable[[aiosqlite.Connection], Awaitable[T]]) -> T:
        if not self._initialized:
            await self.init()
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[T] = loop.create_future()
        await self._write_queue.put((fn, fut))
        return await fut

    async def read(self, fn: Callable[[aiosqlite.Connection], Awaitable[T]]) -> T:
        if not self._initialized:
            await self.init()
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            await conn.execute("PRAGMA busy_timeout=5000;")
            return await fn(conn)

    async def upsert_session(self, session: Session) -> None:
        async def _op(conn: aiosqlite.Connection) -> None:
            await conn.execute(
                """
                INSERT INTO sessions (
                    id, title, user_request, target_workspace, cdp_url, mode,
                    max_rounds, current_round, stage, resume_step, latest_plan_text,
                    final_plan_markdown, executor_prompt, exported_files_json, error,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    title=excluded.title,
                    user_request=excluded.user_request,
                    target_workspace=excluded.target_workspace,
                    cdp_url=excluded.cdp_url,
                    mode=excluded.mode,
                    max_rounds=excluded.max_rounds,
                    current_round=excluded.current_round,
                    stage=excluded.stage,
                    resume_step=excluded.resume_step,
                    latest_plan_text=excluded.latest_plan_text,
                    final_plan_markdown=excluded.final_plan_markdown,
                    executor_prompt=excluded.executor_prompt,
                    exported_files_json=excluded.exported_files_json,
                    error=excluded.error,
                    updated_at=excluded.updated_at;
                """,
                (
                    session.id,
                    session.title,
                    session.user_request,
                    session.target_workspace,
                    session.cdp_url,
                    session.mode.value,
                    session.max_rounds,
                    session.current_round,
                    session.stage.value,
                    session.resume_step,
                    session.latest_plan_text,
                    session.final_plan_markdown,
                    session.executor_prompt,
                    json.dumps(session.exported_files, ensure_ascii=False),
                    session.error,
                    session.created_at,
                    session.updated_at,
                ),
            )

        await self.write(_op)

    async def append_message(
        self,
        session_id: str,
        round_num: int,
        sender: str,
        recipient: str,
        content: str,
        message_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> tuple[RelayMessage, bool]:
        """Insert message in writer queue; returns (RelayMessage, is_duplicate)."""
        msg_id = message_id or uuid.uuid4().hex
        ts = _now_iso()
        meta = metadata or {}

        async def _op(conn: aiosqlite.Connection) -> tuple[RelayMessage, bool]:
            cur = await conn.execute(
                "SELECT message_id, seq, round_num, sender, recipient, content, timestamp, metadata_json "
                "FROM messages WHERE session_id = ? AND message_id = ?;",
                (session_id, msg_id),
            )
            existing = await cur.fetchone()
            if existing:
                return (
                    RelayMessage(
                        id=existing[0],
                        seq=int(existing[1]),
                        round_num=int(existing[2]),
                        sender=existing[3],
                        recipient=existing[4],
                        content=existing[5],
                        timestamp=existing[6],
                        metadata=json.loads(existing[7]),
                    ),
                    True,
                )

            cur = await conn.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 FROM messages WHERE session_id = ?;",
                (session_id,),
            )
            next_seq = int((await cur.fetchone())[0])

            await conn.execute(
                """
                INSERT INTO messages (
                    message_id, session_id, seq, round_num, sender, recipient,
                    content, timestamp, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    msg_id,
                    session_id,
                    next_seq,
                    round_num,
                    sender,
                    recipient,
                    content,
                    ts,
                    json.dumps(meta, ensure_ascii=False),
                ),
            )
            return (
                RelayMessage(
                    id=msg_id,
                    seq=next_seq,
                    round_num=round_num,
                    sender=sender,  # type: ignore[arg-type]
                    recipient=recipient,  # type: ignore[arg-type]
                    content=content,
                    timestamp=ts,
                    metadata=meta,
                ),
                False,
            )

        return await self.write(_op)

    async def save_plan_version(
        self, session_id: str, round_num: int, items: list[PlanItem]
    ) -> None:
        async def _op(conn: aiosqlite.Connection) -> None:
            now = _now_iso()
            await conn.execute(
                "INSERT INTO plan_versions (session_id, round_num, items_json, created_at) VALUES (?, ?, ?, ?);",
                (
                    session_id,
                    round_num,
                    json.dumps([i.model_dump(mode="json") for i in items], ensure_ascii=False),
                    now,
                ),
            )
            for item in items:
                await conn.execute(
                    "INSERT INTO approvals (session_id, round_num, item_id, status, revision_note, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?);",
                    (session_id, round_num, item.id, item.status.value, item.revision_note, now),
                )

        await self.write(_op)

    async def log_event(
        self, session_id: str, event_type: str, details: dict[str, Any]
    ) -> None:
        async def _op(conn: aiosqlite.Connection) -> None:
            await conn.execute(
                "INSERT INTO session_events (session_id, event_type, details_json, created_at) VALUES (?, ?, ?, ?);",
                (session_id, event_type, json.dumps(details, ensure_ascii=False), _now_iso()),
            )

        await self.write(_op)

    async def register_external_chat(
        self,
        session_id: str,
        provider: str,
        conversation_url: str,
        conversation_id: str | None = None,
        title: str | None = None,
        created_by_airoom: bool = True,
    ) -> int:
        async def _op(conn: aiosqlite.Connection) -> int:
            cur = await conn.execute(
                """
                INSERT INTO external_chats (
                    session_id, provider, conversation_id, conversation_url, title,
                    created_by_airoom, deletion_status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'active', ?);
                """,
                (
                    session_id,
                    provider,
                    conversation_id,
                    conversation_url,
                    title,
                    1 if created_by_airoom else 0,
                    _now_iso(),
                ),
            )
            return int(cur.lastrowid or 0)

        return await self.write(_op)

    async def list_external_chats(self, session_id: str) -> list[dict[str, Any]]:
        async def _op(conn: aiosqlite.Connection) -> list[dict[str, Any]]:
            cur = await conn.execute(
                "SELECT id, session_id, provider, conversation_id, conversation_url, title, "
                "created_by_airoom, deletion_status, created_at "
                "FROM external_chats WHERE session_id = ? ORDER BY id ASC;",
                (session_id,),
            )
            rows = await cur.fetchall()
            return [dict(r) for r in rows]

        return await self.read(_op)

    async def mark_external_chats_cleanup_atomic(
        self, session_id: str, requested_ids: list[int]
    ) -> dict[str, Any]:
        """Atomically mark whitelisted AIroom chats for cleanup and audit both successes and refusals."""

        async def _op(conn: aiosqlite.Connection) -> dict[str, Any]:
            now = _now_iso()
            processed: list[dict[str, Any]] = []
            refused: list[dict[str, Any]] = []

            for cid in requested_ids:
                cur = await conn.execute(
                    "SELECT id, provider, conversation_url, created_by_airoom, deletion_status "
                    "FROM external_chats WHERE id = ? AND session_id = ?;",
                    (cid, session_id),
                )
                row = await cur.fetchone()
                if not row:
                    refused.append({"id": cid, "reason": "not_found"})
                    await conn.execute(
                        "INSERT INTO session_events (session_id, event_type, details_json, created_at) VALUES (?, ?, ?, ?);",
                        (session_id, "external_chat_cleanup_refused", json.dumps({"chat_id": cid, "reason": "not_found"}), now),
                    )
                    continue

                if int(row[3]) != 1 or row[4] != "active":
                    reason = "not_whitelisted" if int(row[3]) != 1 else f"invalid_status:{row[4]}"
                    refused.append({"id": cid, "reason": reason})
                    await conn.execute(
                        "INSERT INTO session_events (session_id, event_type, details_json, created_at) VALUES (?, ?, ?, ?);",
                        (session_id, "external_chat_cleanup_refused", json.dumps({"chat_id": cid, "reason": reason}), now),
                    )
                    continue

                await conn.execute(
                    "UPDATE external_chats SET deletion_status = 'marked_for_cleanup' WHERE id = ?;",
                    (cid,),
                )
                await conn.execute(
                    "INSERT INTO session_events (session_id, event_type, details_json, created_at) VALUES (?, ?, ?, ?);",
                    (
                        session_id,
                        "external_chat_marked_for_cleanup",
                        json.dumps({"chat_id": cid, "provider": row[1], "url": row[2]}),
                        now,
                    ),
                )
                processed.append({"id": cid, "status": "marked_for_cleanup"})

            return {"processed": processed, "refused": refused}

        return await self.write(_op)

    async def record_execution(self, session_id: str, status: str, details: str) -> int:
        async def _op(conn: aiosqlite.Connection) -> int:
            cur = await conn.execute(
                "INSERT INTO execution_history (session_id, status, details, created_at) VALUES (?, ?, ?, ?);",
                (session_id, status, details, _now_iso()),
            )
            return int(cur.lastrowid or 0)

        return await self.write(_op)

    async def list_executions(self, session_id: str) -> list[dict[str, Any]]:
        async def _op(conn: aiosqlite.Connection) -> list[dict[str, Any]]:
            cur = await conn.execute(
                "SELECT id, session_id, status, details, created_at "
                "FROM execution_history WHERE session_id = ? ORDER BY id DESC;",
                (session_id,),
            )
            return [dict(r) for r in await cur.fetchall()]

        return await self.read(_op)

    async def save_context_summary(
        self,
        session_id: str,
        from_seq: int,
        to_seq: int,
        summary_content: str,
        decisions: list[str],
        constraints: list[str],
    ) -> str:
        summary_id = uuid.uuid4().hex[:12]

        async def _op(conn: aiosqlite.Connection) -> str:
            await conn.execute(
                """
                INSERT INTO context_summaries (
                    summary_id, session_id, from_seq, to_seq, summary_content,
                    decisions_json, constraints_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    summary_id,
                    session_id,
                    from_seq,
                    to_seq,
                    summary_content,
                    json.dumps(decisions, ensure_ascii=False),
                    json.dumps(constraints, ensure_ascii=False),
                    _now_iso(),
                ),
            )
            return summary_id

        return await self.write(_op)

    async def get_messages_after(self, session_id: str, last_seq: int) -> list[RelayMessage]:
        async def _op(conn: aiosqlite.Connection) -> list[RelayMessage]:
            cur = await conn.execute(
                "SELECT message_id, seq, round_num, sender, recipient, content, timestamp, metadata_json "
                "FROM messages WHERE session_id = ? AND seq > ? ORDER BY seq ASC;",
                (session_id, last_seq),
            )
            rows = await cur.fetchall()
            return [
                RelayMessage(
                    id=r["message_id"],
                    seq=int(r["seq"]),
                    round_num=int(r["round_num"]),
                    sender=r["sender"],
                    recipient=r["recipient"],
                    content=r["content"],
                    timestamp=r["timestamp"],
                    metadata=json.loads(r["metadata_json"]),
                )
                for r in rows
            ]

        return await self.read(_op)

    async def load_all_sessions(self) -> list[Session]:
        async def _op(conn: aiosqlite.Connection) -> list[Session]:
            cur = await conn.execute("SELECT * FROM sessions ORDER BY created_at DESC;")
            s_rows = await cur.fetchall()
            sessions: list[Session] = []
            for r in s_rows:
                sid = r["id"]
                m_cur = await conn.execute(
                    "SELECT message_id, seq, round_num, sender, recipient, content, timestamp, metadata_json "
                    "FROM messages WHERE session_id = ? ORDER BY seq ASC;",
                    (sid,),
                )
                msgs = [
                    RelayMessage(
                        id=m["message_id"],
                        seq=int(m["seq"]),
                        round_num=int(m["round_num"]),
                        sender=m["sender"],
                        recipient=m["recipient"],
                        content=m["content"],
                        timestamp=m["timestamp"],
                        metadata=json.loads(m["metadata_json"]),
                    )
                    for m in await m_cur.fetchall()
                ]
                p_cur = await conn.execute(
                    "SELECT items_json FROM plan_versions WHERE session_id = ? ORDER BY id DESC LIMIT 1;",
                    (sid,),
                )
                p_row = await p_cur.fetchone()
                plan_items = (
                    [PlanItem(**it) for it in json.loads(p_row["items_json"])]
                    if p_row
                    else []
                )
                sessions.append(
                    Session(
                        id=sid,
                        title=r["title"],
                        user_request=r["user_request"],
                        target_workspace=r["target_workspace"],
                        cdp_url=r["cdp_url"],
                        mode=SessionMode(r["mode"]),
                        max_rounds=int(r["max_rounds"]),
                        current_round=int(r["current_round"]),
                        stage=SessionStage(r["stage"]),
                        resume_step=r["resume_step"],
                        latest_plan_text=r["latest_plan_text"],
                        plan_items=plan_items,
                        messages=msgs,
                        final_plan_markdown=r["final_plan_markdown"],
                        executor_prompt=r["executor_prompt"],
                        exported_files=json.loads(r["exported_files_json"]),
                        error=r["error"],
                        created_at=r["created_at"],
                        updated_at=r["updated_at"],
                    )
                )
            return sessions

        return await self.read(_op)

    async def backup_database(self, backup_dir: str | Path, tag: str = "manual") -> Path:
        bdir = Path(backup_dir)
        bdir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        dest = bdir / f"airoom_v{SUPPORTED_SCHEMA_VERSION}_{tag}_{ts}.db"

        def _do_vacuum() -> None:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(f"VACUUM INTO '{dest.as_posix()}';")

        await asyncio.to_thread(_do_vacuum)
        try:
            os.chmod(dest, 0o600)
        except OSError:
            pass
        return dest

    @staticmethod
    def verify_restore_isolated(
        backup_path: str | Path,
        temp_dir: str | Path,
        source_db: str | Path | None = None,
    ) -> bool:
        """Restore backup into an isolated temp directory and verify integrity, schema_version, and row counts."""
        src = Path(backup_path)
        tdir = Path(temp_dir)
        tdir.mkdir(parents=True, exist_ok=True)
        restored = tdir / f"verify_{src.name}"
        try:
            with sqlite3.connect(src) as s_conn, sqlite3.connect(restored) as d_conn:
                s_conn.backup(d_conn)
                res = d_conn.execute("PRAGMA integrity_check;").fetchone()
                if not (res and res[0] == "ok"):
                    return False
                if source_db is not None:
                    with sqlite3.connect(source_db) as orig_conn:
                        for query in (
                            "SELECT COALESCE(MAX(version), 0) FROM schema_version;",
                            "SELECT COUNT(*) FROM sessions;",
                            "SELECT COUNT(*) FROM messages;",
                        ):
                            if d_conn.execute(query).fetchone() != orig_conn.execute(query).fetchone():
                                return False
            return True
        except sqlite3.DatabaseError:
            return False
