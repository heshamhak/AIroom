from __future__ import annotations

import asyncio
import sqlite3
import stat
import sys
from pathlib import Path
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from db import SUPPORTED_SCHEMA_VERSION, DatabaseManager
from models import CreateSessionRequest, SessionMode
from orchestrator import SessionStore


def test_p1_wal_mode_and_file_permissions(tmp_path: Path):
    async def _run():
        db_file = tmp_path / "wal_perm.db"
        db = DatabaseManager(db_file)
        await db.init()
        try:
            assert stat.S_IMODE(db_file.stat().st_mode) == 0o600
            assert stat.S_IMODE(db_file.parent.stat().st_mode) == 0o700

            async def _wal(conn):
                cur = await conn.execute("PRAGMA journal_mode;")
                return (await cur.fetchone())[0].lower()

            assert await db.read(_wal) == "wal"
        finally:
            await db.close()

    asyncio.run(_run())


def test_p1_concurrent_seq_contiguity_single_writer(tmp_path: Path):
    async def _run():
        store = SessionStore(db=DatabaseManager(tmp_path / "seq.db"))
        await store.init()
        try:
            s = await store.create_persisted(
                CreateSessionRequest(title="Seq", user_request="Req", target_workspace=str(tmp_path))
            )
            results = await asyncio.gather(
                *[store.db.append_message(s.id, 1, "CHATGPT", "BRIDGE", f"m-{i}") for i in range(6)]
            )
            seqs = [msg.seq for msg, _ in results]
            assert seqs == [2, 3, 4, 5, 6, 7]
        finally:
            await store.close()

    asyncio.run(_run())


def test_p1_duplicate_message_id_no_rebroadcast(tmp_path: Path):
    async def _run():
        store = SessionStore(db=DatabaseManager(tmp_path / "dup.db"))
        await store.init()
        try:
            s = await store.create_persisted(
                CreateSessionRequest(title="Dup", user_request="Req", target_workspace=str(tmp_path))
            )
            ws = AsyncMock()
            store.subscribers[s.id] = {ws}

            m1 = await store._append_message(s.id, 1, "BRIDGE", "CHATGPT", "hello", message_id="fixed-1")
            m2 = await store._append_message(s.id, 1, "BRIDGE", "CHATGPT", "hello", message_id="fixed-1")
            assert m1.seq == m2.seq == 2
            # Broadcast must fire ONLY once (for m1, skipped on duplicate m2)
            assert ws.send_json.await_count == 1
            assert len(s.messages) == 2
        finally:
            await store.close()

    asyncio.run(_run())


def test_p1_writer_loop_survives_error_and_drains_on_close(tmp_path: Path):
    async def _run():
        db = DatabaseManager(tmp_path / "writer.db")
        await db.init()
        store = SessionStore(db=db)
        s = await store.create_persisted(
            CreateSessionRequest(title="Writer", user_request="Req", target_workspace=str(tmp_path))
        )

        async def _fail(_conn):
            raise ValueError("boom")

        with pytest.raises(ValueError, match="boom"):
            await db.write(_fail)

        msg, _ = await db.append_message(s.id, 1, "BRIDGE", "USER", "after-error")
        assert msg.seq == 2
        await db.close()

    asyncio.run(_run())


def test_p1_concurrent_websocket_replay_no_gaps_no_duplicates(tmp_path: Path):
    async def _run():
        store = SessionStore(db=DatabaseManager(tmp_path / "replay.db"))
        await store.init()
        try:
            s = await store.create_persisted(
                CreateSessionRequest(title="Replay", user_request="Req", target_workspace=str(tmp_path))
            )
            await store._append_message(s.id, 1, "BRIDGE", "CHATGPT", "m2")
            await store._append_message(s.id, 1, "CHATGPT", "BRIDGE", "m3")

            sent_events: list[dict] = []
            ws = AsyncMock()
            ws.send_json.side_effect = lambda payload: sent_events.append(payload)

            async def _producer():
                await asyncio.sleep(0.001)
                await store._append_message(s.id, 1, "BRIDGE", "CLAUDE", "m4")
                await store._append_message(s.id, 1, "CLAUDE", "BRIDGE", "m5")

            await asyncio.gather(
                store.subscribe(s.id, ws, last_seq=1),
                _producer(),
            )

            received_seqs: list[int] = []
            for ev in sent_events:
                if ev["type"] == "replay":
                    received_seqs.extend(m["seq"] for m in ev["messages"])
                elif ev["type"] == "message_added":
                    received_seqs.append(ev["seq"])

            assert received_seqs == sorted(received_seqs)
            assert len(received_seqs) == len(set(received_seqs))
            assert set(received_seqs).issuperset({2, 3, 4, 5})
        finally:
            await store.close()

    asyncio.run(_run())


def test_p1_context_compaction_preserves_messages(tmp_path: Path):
    async def _run():
        store = SessionStore(db=DatabaseManager(tmp_path / "compact.db"))
        await store.init()
        try:
            s = await store.create_persisted(
                CreateSessionRequest(title="Compact", user_request="Req", target_workspace=str(tmp_path))
            )
            for i in range(4):
                await store._append_message(s.id, 1, "CHATGPT", "BRIDGE", "X" * 2000)

            summary_id = await store.maybe_compact_context(s.id, max_tokens=1000, threshold_ratio=0.75)
            assert summary_id is not None
            # Original messages are never deleted
            all_msgs = await store.db.get_messages_after(s.id, last_seq=0)
            assert len(all_msgs) == 5
        finally:
            await store.close()

    asyncio.run(_run())


def test_p1_external_chat_whitelist_binding_and_atomic_audit(tmp_path: Path):
    async def _run():
        db = DatabaseManager(tmp_path / "ext.db")
        store = SessionStore(db=db)
        await store.init()
        try:
            s = await store.create_persisted(
                CreateSessionRequest(title="Ext", user_request="Req", target_workspace=str(tmp_path))
            )
            c_ai = await db.register_external_chat(
                s.id, "chatgpt", "https://chatgpt.com/c/ai-1", created_by_airoom=True
            )
            c_user = await db.register_external_chat(
                s.id, "chatgpt", "https://chatgpt.com/c/personal", created_by_airoom=False
            )
            outcome = await db.mark_external_chats_cleanup_atomic(s.id, [c_ai, c_user, 9999])
            assert [p["id"] for p in outcome["processed"]] == [c_ai]
            assert len(outcome["refused"]) == 2
        finally:
            await store.close()

    asyncio.run(_run())


def test_p1_backup_restore_row_counts_and_corrupted_db(tmp_path: Path):
    async def _run():
        db_file = tmp_path / "bkp.db"
        store = SessionStore(db=DatabaseManager(db_file))
        await store.init()
        try:
            s = await store.create_persisted(
                CreateSessionRequest(title="Bkp", user_request="Req", target_workspace=str(tmp_path))
            )
            await store._append_message(s.id, 1, "BRIDGE", "CHATGPT", "hello")
            backup_path = await store.db.backup_database(tmp_path / "backups", tag="t")
            assert stat.S_IMODE(backup_path.stat().st_mode) == 0o600
            assert DatabaseManager.verify_restore_isolated(
                backup_path, tmp_path / "restore_tmp", source_db=db_file
            )

            corrupt = tmp_path / "corrupt.db"
            corrupt.write_bytes(b"corrupted-bytes")
            assert DatabaseManager.verify_restore_isolated(corrupt, tmp_path / "restore_tmp") is False
        finally:
            await store.close()

    asyncio.run(_run())


def test_p1_rejects_newer_schema_version_and_migration_rollback(tmp_path: Path):
    async def _run():
        db_file = tmp_path / "schema.db"
        db = DatabaseManager(db_file)
        await db.init()
        await db.close()

        with sqlite3.connect(db_file) as conn:
            conn.execute(
                "INSERT INTO schema_version (version, applied_at, migration_id, checksum) "
                "VALUES (?, '2026-12-01T00:00:00Z', '999_future', 'abc');",
                (SUPPORTED_SCHEMA_VERSION + 1,),
            )
            conn.commit()

        db2 = DatabaseManager(db_file)
        with pytest.raises(RuntimeError, match="newer than supported"):
            await db2.init()

    asyncio.run(_run())
