from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any
from fastapi import WebSocket

from cdp_bridge import CdpBrowserRelay, QuotaExhaustedError, ensure_chrome_cdp
from db import DatabaseManager
from executor import export_session_artifacts
from repo_indexer import build_repo_map
from runner import build_self_heal_prompt, run_verification
from models import (
    CreateSessionRequest,
    MarkerStatus,
    RelayMessage,
    Session,
    SessionMode,
    SessionStage,
)
from protocol import (
    apply_claude_validation,
    build_chatgpt_initial_prompt,
    build_chatgpt_revision_prompt,
    build_claude_validation_prompt,
    compact_relay_text,
    parse_plan_items,
)


class SessionStore:
    """Persistent SQLite-backed session store + Write-Then-Broadcast WebSocket hub."""

    def __init__(self, db: DatabaseManager | None = None) -> None:
        self.db = db or DatabaseManager()
        self.sessions: dict[str, Session] = {}
        self.subscribers: dict[str, set[WebSocket]] = {}
        self._live_buffers: dict[WebSocket, list[dict[str, Any]]] = {}
        self.tasks: dict[str, asyncio.Task[Any]] = {}
        self.pause_flags: dict[str, bool] = {}

    async def init(self) -> None:
        await self.db.init()
        loaded = await self.db.load_all_sessions()
        for s in loaded:
            self.sessions[s.id] = s

    async def close(self) -> None:
        await self.db.close()

    def create(self, req: CreateSessionRequest) -> Session:
        sid = uuid.uuid4().hex[:10]
        session = Session(
            id=sid,
            title=req.title.strip(),
            user_request=req.user_request.strip(),
            target_workspace=req.target_workspace.strip() or ".",
            cdp_url=req.cdp_url.strip(),
            mode=req.mode,
            max_rounds=max(1, min(req.max_rounds, 10)),
            verify_command=req.verify_command.strip(),
        )
        session.messages.append(
            RelayMessage(
                id=uuid.uuid4().hex[:8],
                seq=1,
                round_num=0,
                sender="USER",
                recipient="BRIDGE",
                content=session.user_request,
            )
        )
        self.sessions[sid] = session
        return session

    async def create_persisted(self, req: CreateSessionRequest) -> Session:
        session = self.create(req)
        first_msg = session.messages[0]
        await self.db.upsert_session(session)
        await self.db.append_message(
            session_id=session.id,
            round_num=0,
            sender="USER",
            recipient="BRIDGE",
            content=first_msg.content,
            message_id=first_msg.id,
        )
        return session

    def get(self, sid: str) -> Session | None:
        return self.sessions.get(sid)

    def list_all(self) -> list[Session]:
        return sorted(self.sessions.values(), key=lambda s: s.created_at, reverse=True)

    async def subscribe(self, sid: str, ws: WebSocket, last_seq: int | None = None) -> None:
        await ws.accept()
        session = self.sessions.get(sid)
        if not session:
            self.subscribers.setdefault(sid, set()).add(ws)
            return

        if last_seq is not None:
            # Buffer live broadcasts during replay to prevent replay/live race
            live_buffer: list[dict[str, Any]] = []
            self._live_buffers[ws] = live_buffer
            self.subscribers.setdefault(sid, set()).add(ws)
            try:
                missed = await self.db.get_messages_after(sid, last_seq)
                last_replayed = missed[-1].seq if missed else last_seq
                await ws.send_json(
                    {
                        "type": "replay",
                        "last_seq": last_seq,
                        "messages": [m.model_dump(mode="json") for m in missed],
                        "session": session.model_dump(mode="json"),
                    }
                )
                while True:
                    if not live_buffer:
                        # Remove buffer synchronously with empty check (no await in between)
                        self._live_buffers.pop(ws, None)
                        break
                    batch = list(live_buffer)
                    live_buffer.clear()
                    for buffered in batch:
                        msg_seq = buffered.get("seq")
                        if msg_seq is None or int(msg_seq) > last_replayed:
                            if msg_seq is not None:
                                last_replayed = max(last_replayed, int(msg_seq))
                            await ws.send_json(buffered)
            finally:
                self._live_buffers.pop(ws, None)
        else:
            self.subscribers.setdefault(sid, set()).add(ws)
            await ws.send_json({"type": "snapshot", "session": session.model_dump(mode="json")})

    def unsubscribe(self, sid: str, ws: WebSocket) -> None:
        self._live_buffers.pop(ws, None)
        if sid in self.subscribers:
            self.subscribers[sid].discard(ws)

    async def broadcast(self, sid: str, event_type: str, payload: dict[str, Any] | None = None) -> None:
        session = self.sessions.get(sid)
        if not session:
            return
        session.updated_at = datetime.now(timezone.utc).isoformat()
        if self.db._initialized:
            await self.db.upsert_session(session)
        msg = {
            "type": event_type,
            "session": session.model_dump(mode="json"),
            **(payload or {}),
        }
        dead: list[WebSocket] = []
        for ws in list(self.subscribers.get(sid, ())):
            if ws in self._live_buffers:
                self._live_buffers[ws].append(msg)
                continue
            try:
                await ws.send_json(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.unsubscribe(sid, ws)

    async def _append_message(
        self,
        sid: str,
        round_num: int,
        sender: str,
        recipient: str,
        content: str,
        message_id: str | None = None,
    ) -> RelayMessage:
        """Write-Then-Broadcast: persist to SQLite with seq before broadcasting; skip broadcast on duplicate."""
        session = self.sessions[sid]
        msg_id = message_id or uuid.uuid4().hex[:12]
        is_duplicate = False
        if self.db._initialized:
            saved_msg, is_duplicate = await self.db.append_message(
                session_id=sid,
                round_num=round_num,
                sender=sender,
                recipient=recipient,
                content=content,
                message_id=msg_id,
            )
            if is_duplicate:
                return saved_msg
            try:
                await self.maybe_compact_context(sid)
            except Exception:
                pass
        else:
            saved_msg = RelayMessage(
                id=msg_id,
                seq=len(session.messages) + 1,
                round_num=round_num,
                sender=sender,  # type: ignore[arg-type]
                recipient=recipient,  # type: ignore[arg-type]
                content=content,
            )
        session.messages.append(saved_msg)
        await self.broadcast(
            sid,
            "message_added",
            {"seq": saved_msg.seq, "message": saved_msg.model_dump(mode="json")},
        )
        return saved_msg

    async def maybe_compact_context(
        self,
        sid: str,
        max_tokens: int = 6000,
        threshold_ratio: float = 0.75,
    ) -> str | None:
        """Save structured context summary when estimated tokens exceed threshold (never deletes messages)."""
        session = self.sessions.get(sid)
        if not session or len(session.messages) < 4:
            return None
        est_tokens = sum(len(m.content) for m in session.messages) // 4
        if est_tokens < int(max_tokens * threshold_ratio):
            return None

        from_seq = session.messages[0].seq or 1
        to_seq = session.messages[-1].seq or len(session.messages)
        decisions = [
            f"{i.id}: {i.title} [{i.status.value}]" for i in session.plan_items
        ]
        constraints = [
            f"Workspace: {session.target_workspace}",
            f"Round: {session.current_round}/{session.max_rounds}",
        ]
        summary_text = f"Compact state for '{session.title}' (seq {from_seq}-{to_seq}, ~{est_tokens} tokens)."
        return await self.db.save_context_summary(
            session_id=sid,
            from_seq=from_seq,
            to_seq=to_seq,
            summary_content=summary_text,
            decisions=decisions,
            constraints=constraints,
        )

    def request_pause(self, sid: str) -> None:
        self.pause_flags[sid] = True

    async def start(self, sid: str) -> None:
        session = self.sessions.get(sid)
        if not session:
            raise KeyError("Session not found")
        existing = self.tasks.get(sid)
        if existing and not existing.done():
            return
        self.pause_flags[sid] = False
        self.tasks[sid] = asyncio.create_task(self._run_loop(sid))

    async def _run_loop(self, sid: str) -> None:
        session = self.sessions[sid]
        session.error = None

        try:
            if session.mode == SessionMode.SIMULATION:
                await self._run_simulation(sid)
            else:
                await self._run_live_cdp(sid)
        except QuotaExhaustedError as exc:
            session.stage = SessionStage.PAUSED
            session.error = (
                f"{exc} — تم حفظ التقدم عند ({session.resume_step} / جولة {session.current_round}). "
                f"بدّل الحساب أو انتظر ثم اضغط تشغيل للاستكمال من نفس النقطة."
            )
            await self._append_message(sid, session.current_round, "BRIDGE", "USER", session.error)
            await self.broadcast(sid, "stage_changed")
        except Exception as exc:
            session.stage = SessionStage.FAILED
            session.error = str(exc)
            await self.broadcast(sid, "error", {"error": str(exc)})

    async def _run_simulation(self, sid: str) -> None:
        """Simulated 2-round ChatGPT <-> Claude negotiation for offline/demo testing."""
        session = self.sessions[sid]

        # Round 1: ChatGPT Initial Plan
        session.current_round = 1
        session.stage = SessionStage.PLANNING_CHATGPT
        await self.broadcast(sid, "stage_changed")

        chatgpt_prompt = build_chatgpt_initial_prompt(
            session.title, session.user_request, session.target_workspace
        )
        await self._append_message(sid, 1, "BRIDGE", "CHATGPT", chatgpt_prompt)
        await asyncio.sleep(1.0)

        round1_plan = (
            f'[PLAN_ITEM id="P1" title="هيكلة المشروع والبيئة الأساسية"]\n'
            f'إنشاء هيكل المجلدات لمشروع ({session.title}) داخل {session.target_workspace} وتهيئة ملفات الإعدادات والاعتماديات.\n'
            f'[AGREE_REQUEST id="P1" from="ChatGPT" to="Claude"]\n'
            f'[/PLAN_ITEM]\n\n'
            f'[PLAN_ITEM id="P2" title="بناء منطق العمل الأساسي وواجهة الربط"]\n'
            f'تنفيذ الوحدات الأساسية المطلوبة لتحقيق: {session.user_request}\n'
            f'[AGREE_REQUEST id="P2" from="ChatGPT" to="Claude"]\n'
            f'[/PLAN_ITEM]\n\n'
            f'[PLAN_ITEM id="P3" title="الاختبارات والتحقق النهائي"]\n'
            f'كتابة اختبارات الوحدة والتكامل والتأكد من خلو المشروع من الأخطاء.\n'
            f'[AGREE_REQUEST id="P3" from="ChatGPT" to="Claude"]\n'
            f'[/PLAN_ITEM]'
        )
        session.plan_items = parse_plan_items(round1_plan, session.plan_items)
        await self._append_message(sid, 1, "CHATGPT", "BRIDGE", round1_plan)

        if self.pause_flags.get(sid):
            session.stage = SessionStage.PAUSED
            await self.broadcast(sid, "stage_changed")
            return

        # Round 1: Claude Validation (requests 1 revision on P2)
        session.stage = SessionStage.VALIDATING_CLAUDE
        await self.broadcast(sid, "stage_changed")

        claude_prompt_1 = build_claude_validation_prompt(
            session.title, session.user_request, round1_plan, 1
        )
        await self._append_message(sid, 1, "BRIDGE", "CLAUDE", claude_prompt_1)
        await asyncio.sleep(1.0)

        claude_resp_1 = (
            '[REVIEW_ITEM id="P1"]\n'
            '[STATUS: APPROVED]\n'
            '[REVISION_NOTE id="P1"]الهيكلة واضحة ومناسبة.[/REVISION_NOTE]\n'
            '[/REVIEW_ITEM]\n\n'
            '[REVIEW_ITEM id="P2"]\n'
            '[STATUS: NEEDS_REVISION]\n'
            '[REVISION_NOTE id="P2"]يرجى تحديد معالجة الأخطاء (Error Handling) وحالات الفشل الشبكي بوضوح في P2.[/REVISION_NOTE]\n'
            '[/REVIEW_ITEM]\n\n'
            '[REVIEW_ITEM id="P3"]\n'
            '[STATUS: APPROVED]\n'
            '[REVISION_NOTE id="P3"]خطة الاختبارات مقبولة.[/REVISION_NOTE]\n'
            '[/REVIEW_ITEM]'
        )
        session.plan_items, all_ok = apply_claude_validation(session.plan_items, claude_resp_1)
        await self._append_message(sid, 1, "CLAUDE", "BRIDGE", claude_resp_1)

        if not all_ok and session.max_rounds >= 2:
            # Round 2: ChatGPT Revision
            session.current_round = 2
            session.stage = SessionStage.REVISING_CHATGPT
            await self.broadcast(sid, "stage_changed")

            rev_prompt = build_chatgpt_revision_prompt(2, claude_resp_1, ["P2"])
            await self._append_message(sid, 2, "BRIDGE", "CHATGPT", rev_prompt)
            await asyncio.sleep(1.0)

            round2_plan = (
                f'[PLAN_ITEM id="P1" title="هيكلة المشروع والبيئة الأساسية"]\n'
                f'إنشاء هيكل المجلدات لمشروع ({session.title}) داخل {session.target_workspace} وتهيئة ملفات الإعدادات والاعتماديات.\n'
                f'[AGREE_REQUEST id="P1" from="ChatGPT" to="Claude"]\n'
                f'[/PLAN_ITEM]\n\n'
                f'[PLAN_ITEM id="P2" title="بناء منطق العمل الأساسي وواجهة الربط مع معالجة الأخطاء"]\n'
                f'تنفيذ الوحدات الأساسية المطلوبة لتحقيق: {session.user_request}، مع إضافة طبقة Retry ومعالجة استثناءات الشبكة وتسجيل الأخطاء.\n'
                f'[AGREE_REQUEST id="P2" from="ChatGPT" to="Claude"]\n'
                f'[/PLAN_ITEM]\n\n'
                f'[PLAN_ITEM id="P3" title="الاختبارات والتحقق النهائي"]\n'
                f'كتابة اختبارات الوحدة والتكامل والتأكد من خلو المشروع من الأخطاء.\n'
                f'[AGREE_REQUEST id="P3" from="ChatGPT" to="Claude"]\n'
                f'[/PLAN_ITEM]'
            )
            session.plan_items = parse_plan_items(round2_plan, session.plan_items)
            await self._append_message(sid, 2, "CHATGPT", "BRIDGE", round2_plan)

            # Round 2: Claude Final Approval
            session.stage = SessionStage.VALIDATING_CLAUDE
            await self.broadcast(sid, "stage_changed")

            claude_prompt_2 = build_claude_validation_prompt(
                session.title, session.user_request, round2_plan, 2
            )
            await self._append_message(sid, 2, "BRIDGE", "CLAUDE", claude_prompt_2)
            await asyncio.sleep(1.0)

            claude_resp_2 = (
                '[REVIEW_ITEM id="P1"]\n'
                '[STATUS: APPROVED]\n'
                '[REVISION_NOTE id="P1"]معتمد.[/REVISION_NOTE]\n'
                '[/REVIEW_ITEM]\n\n'
                '[REVIEW_ITEM id="P2"]\n'
                '[STATUS: APPROVED]\n'
                '[REVISION_NOTE id="P2"]تمت إضافة معالجة الأخطاء بنجاح. معتمد.[/REVISION_NOTE]\n'
                '[/REVIEW_ITEM]\n\n'
                '[REVIEW_ITEM id="P3"]\n'
                '[STATUS: APPROVED]\n'
                '[REVISION_NOTE id="P3"]معتمد.[/REVISION_NOTE]\n'
                '[/REVIEW_ITEM]\n\n'
                '[CONSENSUS_FINAL: ALL_APPROVED]'
            )
            session.plan_items, all_ok = apply_claude_validation(session.plan_items, claude_resp_2)
            await self._append_message(sid, 2, "CLAUDE", "BRIDGE", claude_resp_2)

        if all_ok:
            session.stage = SessionStage.CONSENSUS_REACHED
            plan_md, exec_prompt, files = export_session_artifacts(session)
            session.final_plan_markdown = plan_md
            session.executor_prompt = exec_prompt
            session.exported_files = files
            await self._append_message(
                sid,
                session.current_round,
                "BRIDGE",
                "EXECUTOR",
                f"اكتمل الإجماع (ALL_APPROVED). تم تجهيز الخطة للتنفيذ في:\n" + "\n".join(files),
            )
        else:
            session.stage = SessionStage.PAUSED
        await self.broadcast(sid, "stage_changed")

    async def _run_live_cdp(self, sid: str) -> None:
        """Live browser automation loop driving ChatGPT and Claude tabs over Chrome CDP."""
        session = self.sessions[sid]
        cdp_status = await ensure_chrome_cdp(session.cdp_url)
        if not cdp_status.connected:
            raise RuntimeError(
                f"تعذر تفعيل وضع المطور (CDP) في Chrome تلقائياً على {session.cdp_url}: {cdp_status.error}"
            )

        async with CdpBrowserRelay(session.cdp_url) as relay:
            # Round 1: ChatGPT opens a new project/chat and generates the initial plan (if not already done)
            if not session.latest_plan_text:
                session.current_round = 1
                session.resume_step = "chatgpt_plan"
                session.stage = SessionStage.PLANNING_CHATGPT
                await self.broadcast(sid, "stage_changed")

                repo_map = build_repo_map(session.target_workspace, max_files=25, max_chars=1800)
                chatgpt_prompt = build_chatgpt_initial_prompt(
                    session.title,
                    f"{session.user_request}\n\n{repo_map}",
                    session.target_workspace,
                )
                await self._append_message(sid, 1, "BRIDGE", "CHATGPT", chatgpt_prompt)

                raw_plan = await relay.send_to_chatgpt(chatgpt_prompt, new_chat=True)
                session.latest_plan_text = compact_relay_text(raw_plan)
                session.plan_items = parse_plan_items(session.latest_plan_text, session.plan_items)
                session.resume_step = "claude_validate"
                await self._append_message(sid, 1, "CHATGPT", "BRIDGE", session.latest_plan_text)

            start_round = max(1, session.current_round)
            for round_num in range(start_round, session.max_rounds + 1):
                if self.pause_flags.get(sid):
                    session.stage = SessionStage.PAUSED
                    await self.broadcast(sid, "stage_changed")
                    return

                session.current_round = round_num
                if session.resume_step != "chatgpt_revise":
                    session.resume_step = "claude_validate"
                    session.stage = SessionStage.VALIDATING_CLAUDE
                    await self.broadcast(sid, "stage_changed")

                    claude_prompt = build_claude_validation_prompt(
                        session.title, session.user_request, session.latest_plan_text, round_num
                    )
                    await self._append_message(sid, round_num, "BRIDGE", "CLAUDE", claude_prompt)

                    raw_claude = await relay.send_to_claude(claude_prompt, new_chat=(round_num == 1))
                    claude_resp = compact_relay_text(raw_claude)
                    session.plan_items, all_approved = apply_claude_validation(
                        session.plan_items, claude_resp
                    )
                    await self._append_message(sid, round_num, "CLAUDE", "BRIDGE", claude_resp)

                    if all_approved:
                        session.stage = SessionStage.CONSENSUS_REACHED
                        plan_md, exec_prompt, files = export_session_artifacts(session)
                        session.final_plan_markdown = plan_md
                        session.executor_prompt = exec_prompt
                        session.exported_files = files
                        await self._append_message(
                            sid,
                            round_num,
                            "BRIDGE",
                            "EXECUTOR",
                            "اكتمل الإجماع من ChatGPT و Claude. الخطة جاهزة للمنفّذ (Antigravity).",
                        )
                        await self.broadcast(sid, "stage_changed")
                        return

                    if round_num >= session.max_rounds:
                        break
                    session.resume_step = "chatgpt_revise"

                # Relay Claude's revision notes back to ChatGPT
                pending_ids = [
                    i.id
                    for i in session.plan_items
                    if i.status != MarkerStatus.APPROVED
                ]
                last_claude_msg = next(
                    (m.content for m in reversed(session.messages) if m.sender == "CLAUDE"),
                    "",
                )
                session.stage = SessionStage.REVISING_CHATGPT
                await self.broadcast(sid, "stage_changed")

                rev_prompt = build_chatgpt_revision_prompt(
                    round_num + 1, last_claude_msg, pending_ids
                )
                await self._append_message(sid, round_num + 1, "BRIDGE", "CHATGPT", rev_prompt)

                raw_rev = await relay.send_to_chatgpt(rev_prompt, new_chat=False)
                session.latest_plan_text = compact_relay_text(raw_rev)
                session.plan_items = parse_plan_items(session.latest_plan_text, session.plan_items)
                session.resume_step = "claude_validate"
                await self._append_message(sid, round_num + 1, "CHATGPT", "BRIDGE", session.latest_plan_text)

            session.stage = SessionStage.PAUSED
            await self.broadcast(sid, "stage_changed")

    async def run_closed_loop_verify(self, sid: str, command_override: str | None = None) -> dict[str, Any]:
        """Run allowlisted verification tests; mark VERIFIED_DONE on pass or emit self-heal prompt on fail."""
        session = self.sessions.get(sid)
        if not session:
            raise KeyError("Session not found")

        cmd = (command_override or session.verify_command).strip()
        if not cmd:
            raise ValueError("verify_command is required for closed-loop verification.")

        session.verify_command = cmd
        session.stage = SessionStage.VERIFYING
        await self.broadcast(sid, "stage_changed")

        result = await run_verification(session.target_workspace, cmd)
        if self.db._initialized:
            await self.db.record_execution(
                sid,
                "verified_passed" if result.ok else "verification_failed",
                f"[{result.command}] exit={result.exit_code}\n{result.output}",
            )

        if result.ok:
            session.stage = SessionStage.VERIFIED_DONE
            session.error = None
            await self._append_message(
                sid,
                session.current_round,
                "EXECUTOR",
                "USER",
                f"تم التحقق والاختبار بنجاح ({result.command} | exit=0):\n{result.output[:1000]}",
            )
        else:
            heal_prompt = build_self_heal_prompt(
                session.title, result.command, result.output, session.current_round + 1
            )
            session.stage = SessionStage.REVISING_CHATGPT
            session.error = f"فشل التحقق ({result.command} | exit={result.exit_code}) — تم تجهيز برومبت الإصلاح الذاتي."
            await self._append_message(
                sid,
                session.current_round,
                "BRIDGE",
                "CHATGPT",
                heal_prompt,
            )

        await self.broadcast(sid, "verification_result", {"verified": result.ok, "exit_code": result.exit_code})
        return {
            "ok": result.ok,
            "exit_code": result.exit_code,
            "command": result.command,
            "output": result.output,
            "stage": session.stage.value,
        }
