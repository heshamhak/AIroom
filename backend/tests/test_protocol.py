from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from models import CreateSessionRequest, MarkerStatus, SessionMode
from orchestrator import SessionStore
from protocol import (
    apply_claude_validation,
    build_chatgpt_initial_prompt,
    compact_relay_text,
    parse_plan_items,
)
from executor import export_session_artifacts
from cdp_bridge import QuotaExhaustedError, _has_unclosed_marker


def test_parse_plan_items_and_claude_consensus():
    chatgpt_output = """
[PLAN_ITEM id="P1" title="API Layer"]
Create FastAPI endpoints.
[AGREE_REQUEST id="P1" from="ChatGPT" to="Claude"]
[/PLAN_ITEM]

[PLAN_ITEM id="P2" title="Database"]
Add SQLite persistence.
[AGREE_REQUEST id="P2" from="ChatGPT" to="Claude"]
[/PLAN_ITEM]
"""
    items = parse_plan_items(chatgpt_output)
    assert len(items) == 2
    assert items[0].id == "P1"
    assert items[0].status == MarkerStatus.PENDING

    claude_round_1 = """
[REVIEW_ITEM id="P1"]
[STATUS: APPROVED]
[REVISION_NOTE id="P1"]Looks solid.[/REVISION_NOTE]
[/REVIEW_ITEM]

[REVIEW_ITEM id="P2"]
[STATUS: NEEDS_REVISION]
[REVISION_NOTE id="P2"]Add migration step.[/REVISION_NOTE]
[/REVIEW_ITEM]
"""
    updated, all_ok = apply_claude_validation(items, claude_round_1)
    assert not all_ok
    assert updated[0].status == MarkerStatus.APPROVED
    assert updated[1].status == MarkerStatus.NEEDS_REVISION
    assert "migration" in (updated[1].revision_note or "")

    claude_round_2 = """
[REVIEW_ITEM id="P1"]
[STATUS: APPROVED]
[/REVIEW_ITEM]

[REVIEW_ITEM id="P2"]
[STATUS: APPROVED]
[/REVIEW_ITEM]
[CONSENSUS_FINAL: ALL_APPROVED]
"""
    final_items, all_ok_2 = apply_claude_validation(updated, claude_round_2)
    assert all_ok_2
    assert all(i.status == MarkerStatus.APPROVED for i in final_items)


def test_export_session_artifacts(tmp_path: Path):
    store = SessionStore()
    session = store.create(
        CreateSessionRequest(
            title="Test App",
            user_request="Build a calculator",
            target_workspace=str(tmp_path),
            mode=SessionMode.SIMULATION,
        )
    )
    session.plan_items = parse_plan_items(
        '[PLAN_ITEM id="P1" title="Core"]Implement math ops[/PLAN_ITEM]'
    )
    session.plan_items, _ = apply_claude_validation(
        session.plan_items,
        '[REVIEW_ITEM id="P1"][STATUS: APPROVED][/REVIEW_ITEM][CONSENSUS_FINAL: ALL_APPROVED]',
    )
    plan_md, exec_prompt, files = export_session_artifacts(session)
    assert "Test App" in plan_md
    assert "المنفّذ (Executor)" in exec_prompt
    assert len(files) == 3
    for f in files:
        assert Path(f).exists()


def test_unclosed_marker_and_quota_error():
    assert _has_unclosed_marker('[PLAN_ITEM id="P1" title="A"]partial...') is True
    assert _has_unclosed_marker('[PLAN_ITEM id="P1" title="A"]ok[/PLAN_ITEM]') is False
    assert _has_unclosed_marker('[REVIEW_ITEM id="P1"][STATUS: APPROVED]') is True
    assert _has_unclosed_marker('[REVIEW_ITEM id="P1"][STATUS: APPROVED][/REVIEW_ITEM]') is False

    err = QuotaExhaustedError("Claude", "message limit")
    assert err.provider == "Claude"
    assert "Claude" in str(err)


def test_compact_relay_text_and_english_prompts():
    noisy = "قلت:\n\n  Hello    world  \n\nعرض المزيد\n  [PLAN_ITEM id=\"P1\" title=\"DB\"]  \n  Use   SQLite \n\n[/PLAN_ITEM]\n"
    compacted = compact_relay_text(noisy)
    assert "قلت:" not in compacted
    assert "عرض المزيد" not in compacted
    assert "Hello world" in compacted
    assert "\n\n" not in compacted

    prompt = build_chatgpt_initial_prompt("AIroom", "Build   it", "/home/iot/AIroom")
    assert "Role: Architect/Planner in AIroom" in prompt
    assert "concise, dense technical English" in prompt

    # Fenced code blocks must preserve indentation & blank lines
    fenced = "Intro\n\n```python\ndef f():\n\n    return  1\n```\n\nOutro"
    compacted_fenced = compact_relay_text(fenced)
    assert "def f():\n\n    return  1" in compacted_fenced


def test_headerless_partial_merge_and_not_reached_consensus():
    initial = parse_plan_items(
        '[PLAN_ITEM id="P1" title="Audit"]Scope[/PLAN_ITEM]\n'
        '[PLAN_ITEM id="P2" title="Contract"]Old schema[/PLAN_ITEM]'
    )
    partial_headerless = '[PLAN_ITEM id="P2"]\nNew schema with boot_id.\n[/PLAN_ITEM]'
    merged = parse_plan_items(partial_headerless, initial)
    assert [i.id for i in merged] == ["P1", "P2"]
    assert merged[1].title == "Contract"
    assert "boot_id" in merged[1].details

    claude_mixed = (
        '[REVIEW_ITEM id="P1"][STATUS: APPROVED][/REVIEW_ITEM]\n'
        '[REVIEW_ITEM id="P2"][STATUS: NEEDS_REVISION][/REVIEW_ITEM]\n'
        '[CONSENSUS_FINAL: NOT_REACHED — 1/2 APPROVED]'
    )
    updated, all_ok = apply_claude_validation(merged, claude_mixed)
    assert not all_ok
    assert updated[0].status == MarkerStatus.APPROVED
    assert updated[1].status == MarkerStatus.NEEDS_REVISION
