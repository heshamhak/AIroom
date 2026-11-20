from __future__ import annotations

from dataclasses import dataclass
from providers import OllamaAdapter


@dataclass(frozen=True)
class TieBreakKey:
    session_id: str
    round_num: int
    trigger_kind: str
    target_provider: str

    def as_str(self) -> str:
        return f"{self.session_id}:{self.round_num}:{self.trigger_kind}:{self.target_provider}"


async def run_hybrid_tiebreaker(
    session_id: str,
    round_num: int,
    trigger_kind: str,
    target_provider: str,
    plan_text: str,
    claude_feedback: str,
    adapter: OllamaAdapter | None = None,
) -> tuple[str, str]:
    """Run local Ollama tie-breaker using a provider-scoped idempotency key."""
    key = TieBreakKey(
        session_id=session_id,
        round_num=round_num,
        trigger_kind=trigger_kind,
        target_provider=target_provider,
    ).as_str()

    ollama = adapter or OllamaAdapter()
    prompt = (
        f"Tie-Breaker Arbitration (Key: {key}).\n"
        f"Current Plan:\n{plan_text[:2500]}\n"
        f"Validator Feedback:\n{claude_feedback[:2000]}\n"
        f"Resolve disagreements concisely in [PLAN_ITEM id=\"...\" title=\"...\"]...[AGREE_REQUEST id=\"...\" from=\"TieBreaker\" to=\"Claude\"][/PLAN_ITEM] format."
    )
    reply = await ollama.generate(prompt)
    return key, reply
