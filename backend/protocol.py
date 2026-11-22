from __future__ import annotations

import re
from models import MarkerStatus, PlanItem


_UI_NOISE_LINES = {
    "قلت:",
    "قال chatgpt:",
    "عرض المزيد",
    "copy code",
    "نسخ الكود",
}


def compact_relay_text(text: str) -> str:
    """Sanitize and compact text between models while preserving ``` and ~~~ fenced code blocks."""
    cleaned_lines: list[str] = []
    fence_marker: str | None = None
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if fence_marker is None and (stripped.startswith("```") or stripped.startswith("~~~")):
            fence_marker = stripped[:3]
            cleaned_lines.append(raw_line.rstrip())
            continue
        if fence_marker is not None:
            cleaned_lines.append(raw_line.rstrip())
            if stripped.startswith(fence_marker):
                fence_marker = None
            continue
        line = re.sub(r"[ \t]+", " ", raw_line).strip()
        if not line:
            continue
        if line.lower() in _UI_NOISE_LINES:
            continue
        cleaned_lines.append(line)
    return "\n".join(cleaned_lines)


def build_chatgpt_initial_prompt(title: str, user_request: str, target_workspace: str) -> str:
    """Build a compact English planning prompt for ChatGPT (The Planner)."""
    compact_req = compact_relay_text(user_request)
    return (
        f"Role: Architect/Planner in AIroom. Claude is Validator. Antigravity is Executor only.\n"
        f"Language: Reply in concise, dense technical English (no filler).\n"
        f"Project: {title} | Workspace: {target_workspace}\n"
        f"Request: {compact_req}\n"
        f"Strict output format (numbered P1, P2, ...):\n"
        f'[PLAN_ITEM id="P1" title="Short Title"]\n'
        f"Files, exact steps, acceptance criteria.\n"
        f'[AGREE_REQUEST id="P1" from="ChatGPT" to="Claude"]\n'
        f"[/PLAN_ITEM]"
    )


def build_claude_validation_prompt(
    title: str,
    user_request: str,
    plan_text: str,
    round_num: int,
) -> str:
    """Build a compact English validation prompt for Claude (The Validator)."""
    compact_req = compact_relay_text(user_request)
    compact_plan = compact_relay_text(plan_text)
    return (
        f"Role: Validator/Auditor in AIroom (Round {round_num}).\n"
        f"Language: Reply in concise technical English (no filler).\n"
        f"Project: {title} | Request: {compact_req}\n"
        f"Plan to audit:\n{compact_plan}\n"
        f"For each item (P1, P2, ...) check architecture, security, completeness, and tests. Reply strictly:\n"
        f'[REVIEW_ITEM id="P1"]\n'
        f"[STATUS: APPROVED]\n"
        f'[REVISION_NOTE id="P1"]Concise reason or exact fix required.[/REVISION_NOTE]\n'
        f"[/REVIEW_ITEM]\n"
        f"(Use APPROVED, NEEDS_REVISION, or REJECTED. If ALL items are APPROVED, append: [CONSENSUS_FINAL: ALL_APPROVED])"
    )


def build_chatgpt_revision_prompt(
    round_num: int,
    claude_feedback: str,
    pending_ids: list[str],
) -> str:
    """Build a compact English revision prompt for ChatGPT."""
    ids_str = ", ".join(pending_ids) if pending_ids else "flagged items"
    compact_fb = compact_relay_text(claude_feedback)
    return (
        f"Round {round_num} Revision. Claude flagged: {ids_str}.\n"
        f"Feedback:\n{compact_fb}\n"
        f"Update ONLY the flagged items ({ids_str}) to resolve all notes (already-approved items remain approved and unchanged; do NOT re-emit approved items). "
        f'Reply in concise English with global execution gates (if requested) and ONLY the flagged items ({ids_str}) in [PLAN_ITEM id="Px" title="..."]...[AGREE_REQUEST id="Px" from="ChatGPT" to="Claude"]...[/PLAN_ITEM] format.'
    )


_PLAN_ITEM_RE = re.compile(
    r'\[PLAN_ITEM\s+id=["\']?(?P<id>[^"\'\]\s]+)["\']?(?:\s+title=["\']?(?P<title>[^"\'\]]+)["\']?)?\s*\](?P<body>.*?)\[/PLAN_ITEM\]',
    re.DOTALL | re.IGNORECASE,
)

_AGREE_REQ_RE = re.compile(
    r'\[AGREE_REQUEST\s+id=["\']?(?P<id>[^"\'\]]+)["\']?[^\]]*\]',
    re.IGNORECASE,
)

_REVIEW_ITEM_RE = re.compile(
    r'\[REVIEW_ITEM\s+id=["\']?(?P<id>[^"\'\]]+)["\']?\](?P<body>.*?)\[/REVIEW_ITEM\]',
    re.DOTALL | re.IGNORECASE,
)

_STATUS_RE = re.compile(
    r'\[STATUS:\s*(?P<status>APPROVED|NEEDS_REVISION|REJECTED)\s*\]',
    re.IGNORECASE,
)

_REV_NOTE_RE = re.compile(
    r'\[REVISION_NOTE[^\]]*\](?P<note>.*?)\[/REVISION_NOTE\]',
    re.DOTALL | re.IGNORECASE,
)

_FINAL_CONSENSUS_RE = re.compile(
    r'\[CONSENSUS_FINAL:\s*ALL_APPROVED\s*\]',
    re.IGNORECASE,
)

_NOT_REACHED_RE = re.compile(
    r'\[CONSENSUS_FINAL:[^\]]*NOT_REACHED',
    re.IGNORECASE,
)


def parse_plan_items(text: str, existing: list[PlanItem] | None = None) -> list[PlanItem]:
    """Extract [PLAN_ITEM] blocks from ChatGPT output, merging partial revisions by id."""
    existing_list = list(existing or [])
    existing_map = {item.id: item for item in existing_list}
    parsed_by_id: dict[str, PlanItem] = {}

    for match in _PLAN_ITEM_RE.finditer(text):
        item_id = match.group("id").strip()
        raw_title = (match.group("title") or "").strip()
        body = match.group("body").strip()
        agree_match = _AGREE_REQ_RE.search(body)
        agree_token = agree_match.group(0) if agree_match else None
        clean_details = _AGREE_REQ_RE.sub("", body).strip()

        prev = existing_map.get(item_id)
        if raw_title:
            title = raw_title
        elif prev and prev.title:
            title = prev.title
        else:
            first_line = next((ln.strip() for ln in clean_details.splitlines() if ln.strip()), "")
            title = first_line[:120] if first_line else item_id

        parsed_by_id[item_id] = PlanItem(
            id=item_id,
            title=title,
            details=clean_details,
            status=prev.status if prev else MarkerStatus.PENDING,
            revision_note=prev.revision_note if prev else None,
            agree_token=agree_token,
        )

    if parsed_by_id:
        if existing_list:
            merged: list[PlanItem] = []
            seen: set[str] = set()
            for item in existing_list:
                merged.append(parsed_by_id.get(item.id, item))
                seen.add(item.id)
            for item_id, new_item in parsed_by_id.items():
                if item_id not in seen:
                    merged.append(new_item)
            return merged
        return list(parsed_by_id.values())

    # Fallback if ChatGPT didn't strictly wrap in [PLAN_ITEM]
    if existing_list:
        return existing_list
    if text.strip():
        return [
            PlanItem(
                id="P1",
                title="الخطة المقترحة",
                details=text.strip(),
                status=MarkerStatus.PENDING,
                agree_token='[AGREE_REQUEST id="P1" from="ChatGPT" to="Claude"]',
            )
        ]
    return []


def apply_claude_validation(
    items: list[PlanItem],
    claude_text: str,
) -> tuple[list[PlanItem], bool]:
    """Parse Claude's [REVIEW_ITEM] markers and update PlanItem statuses."""
    reviews: dict[str, tuple[MarkerStatus, str | None]] = {}

    for match in _REVIEW_ITEM_RE.finditer(claude_text):
        item_id = match.group("id").strip()
        body = match.group("body")
        status_match = _STATUS_RE.search(body)
        note_match = _REV_NOTE_RE.search(body)

        status = MarkerStatus.PENDING
        if status_match:
            raw_status = status_match.group("status").upper()
            status = MarkerStatus(raw_status)
        note = note_match.group("note").strip() if note_match else None
        reviews[item_id] = (status, note)

    updated: list[PlanItem] = []
    for item in items:
        if item.id in reviews:
            st, note = reviews[item.id]
            updated.append(
                item.model_copy(update={"status": st, "revision_note": note})
            )
        else:
            # If Claude emitted global ALL_APPROVED without per-item tags
            if _FINAL_CONSENSUS_RE.search(claude_text) and not _NOT_REACHED_RE.search(claude_text):
                updated.append(item.model_copy(update={"status": MarkerStatus.APPROVED}))
            else:
                updated.append(item)

    has_negative_review = any(
        st in (MarkerStatus.NEEDS_REVISION, MarkerStatus.REJECTED)
        for st, _ in reviews.values()
    )
    not_reached = bool(_NOT_REACHED_RE.search(claude_text))

    all_approved = (
        bool(updated)
        and not has_negative_review
        and not not_reached
        and all(i.status == MarkerStatus.APPROVED for i in updated)
    )
    if (
        _FINAL_CONSENSUS_RE.search(claude_text)
        and not has_negative_review
        and not not_reached
        and not any(i.status in (MarkerStatus.NEEDS_REVISION, MarkerStatus.REJECTED) for i in updated)
    ):
        updated = [i.model_copy(update={"status": MarkerStatus.APPROVED}) for i in updated]
        all_approved = bool(updated)

    return updated, all_approved
