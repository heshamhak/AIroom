from __future__ import annotations

import json
import re
from pathlib import Path
from models import Session


_SECRET_PATTERNS = [
    re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]+", re.IGNORECASE),
    re.compile(r"(token\s*[=:]\s*)[^\s,;\"']+", re.IGNORECASE),
    re.compile(r"\bsk-[A-Za-z0-9_\-]{12,}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\b"),
]


def redact_secrets(text: str) -> str:
    """Redact Bearer tokens, API keys, and JWTs from logs and execution output."""
    out = text
    out = _SECRET_PATTERNS[0].sub(r"\1[REDACTED]", out)
    out = _SECRET_PATTERNS[1].sub(r"\1[REDACTED]", out)
    out = _SECRET_PATTERNS[2].sub("[REDACTED_KEY]", out)
    out = _SECRET_PATTERNS[3].sub("[REDACTED_JWT]", out)
    return out


def render_approved_plan_markdown(session: Session) -> str:
    """Render the consensus-approved plan into a clean Markdown document."""
    lines = [
        f"# خطة التنفيذ المعتمدة: {session.title}",
        "",
        f"- **معرّف الجلسة**: `{session.id}`",
        f"- **المخطط (Planner)**: ChatGPT",
        f"- **المدقق والمصادق (Validator)**: Claude",
        f"- **المنفّذ (Executor)**: Antigravity",
        f"- **عدد الجولات**: {session.current_round}",
        "",
        "## طلب المستخدم الأصلي",
        session.user_request,
        "",
        "## البنود المعتمدة (100% Consensus)",
        "",
    ]
    for item in session.plan_items:
        lines.append(f"### [{item.id}] {item.title} — `{item.status.value}`")
        lines.append("")
        lines.append(item.details)
        if item.revision_note:
            lines.append("")
            lines.append(f"> **ملاحظة المصادقة (Claude)**: {item.revision_note}")
        lines.append("")
    return "\n".join(lines)


def build_executor_prompt(session: Session, plan_md_path: str) -> str:
    """Create the ready-to-execute prompt for Antigravity (the Executor)."""
    return (
        f"أنت الآن **المنفّذ (Executor)**.\n"
        f"تم التخطيط لهذا المشروع بواسطة **ChatGPT** وتمت مراجعته والمصادقة الكاملة عليه من قبل **Claude** "
        f"(الجولة {session.current_round}، جميع العلامات: APPROVED).\n\n"
        f"المشروع: {session.title}\n"
        f"مجلد العمل: {session.target_workspace}\n"
        f"ملف الخطة المعتمدة: {plan_md_path}\n\n"
        f"نفّذ البنود التالية بدقة والتزام كامل دون تغيير في الخطة المعتمدة:\n\n"
        + "\n".join(f"- [{i.id}] {i.title}: {i.details}" for i in session.plan_items)
    )


def export_session_artifacts(session: Session) -> tuple[str, str, list[str]]:
    """Write approved_plan.md, approved_plan.json, and executor_prompt.txt."""
    out_dir = Path(session.target_workspace) / ".airoom"
    out_dir.mkdir(parents=True, exist_ok=True)

    plan_md = render_approved_plan_markdown(session)
    plan_md_path = out_dir / f"{session.id}_approved_plan.md"
    plan_md_path.write_text(plan_md, encoding="utf-8")

    plan_json_path = out_dir / f"{session.id}_approved_plan.json"
    plan_json_path.write_text(
        json.dumps(session.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    executor_prompt = build_executor_prompt(session, str(plan_md_path))
    prompt_path = out_dir / f"{session.id}_executor_prompt.txt"
    prompt_path.write_text(executor_prompt, encoding="utf-8")

    return plan_md, executor_prompt, [
        str(plan_md_path),
        str(plan_json_path),
        str(prompt_path),
    ]
