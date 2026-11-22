from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from cdp_bridge import ChatLengthLimitError, QuotaExhaustedError, classify_limit_text
from models import CreateSessionRequest
from orchestrator import SessionStore


def test_classify_chat_length_vs_daily_quota():
    with pytest.raises(ChatLengthLimitError):
        classify_limit_text("Claude", "Your message will exceed the length limit for this chat.")

    with pytest.raises(QuotaExhaustedError) as exc_info:
        classify_limit_text("Claude", "You have reached your message limit. Resets in 4h 30m.")
    assert "4h 30m" in (exc_info.value.reset_hint or "")


def test_auto_new_chat_handoff_on_length_limit():
    async def _run():
        store = SessionStore()
        s = store.create(CreateSessionRequest(title="Handoff", user_request="Build X"))

        calls: list[tuple[str, bool]] = []

        async def _fake_send(prompt: str, new_chat: bool = False) -> str:
            calls.append((prompt, new_chat))
            if not new_chat:
                raise ChatLengthLimitError("Claude", "length limit for this chat")
            return "[REVIEW_ITEM id=\"P1\"][STATUS: APPROVED][/REVIEW_ITEM]"

        relay = AsyncMock()
        relay.send_to_claude.side_effect = _fake_send

        res = await store._send_with_auto_new_chat(relay, s, "claude", "Audit P1", new_chat=False)
        assert "[STATUS: APPROVED]" in res
        assert len(calls) == 2
        assert calls[0][1] is False
        assert calls[1][1] is True
        assert "[CONTEXT HANDOFF]" in calls[1][0]

    asyncio.run(_run())
