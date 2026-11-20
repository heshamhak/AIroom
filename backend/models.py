from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal
from pydantic import BaseModel, Field


class SessionMode(str, Enum):
    LIVE_CDP = "live_cdp"
    SIMULATION = "simulation"


class SessionStage(str, Enum):
    IDLE = "idle"
    PLANNING_CHATGPT = "planning_chatgpt"
    VALIDATING_CLAUDE = "validating_claude"
    REVISING_CHATGPT = "revising_chatgpt"
    CONSENSUS_REACHED = "consensus_reached"
    EXPORTED = "exported"
    EXECUTING = "executing"
    VERIFYING = "verifying"
    VERIFIED_DONE = "verified_done"
    PAUSED = "paused"
    FAILED = "failed"


class MarkerStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    NEEDS_REVISION = "NEEDS_REVISION"
    REJECTED = "REJECTED"


class PlanItem(BaseModel):
    id: str
    title: str
    details: str
    status: MarkerStatus = MarkerStatus.PENDING
    revision_note: str | None = None
    agree_token: str | None = None


class RelayMessage(BaseModel):
    id: str
    seq: int = 0
    round_num: int
    sender: Literal["USER", "BRIDGE", "CHATGPT", "CLAUDE", "EXECUTOR"]
    recipient: Literal["USER", "BRIDGE", "CHATGPT", "CLAUDE", "EXECUTOR"]
    content: str
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: dict[str, Any] = Field(default_factory=dict)


class Session(BaseModel):
    id: str
    title: str
    user_request: str
    target_workspace: str
    cdp_url: str = "http://127.0.0.1:9223"
    mode: SessionMode = SessionMode.LIVE_CDP
    max_rounds: int = 4
    verify_command: str = ""
    verify_command: str = ""
    current_round: int = 0
    stage: SessionStage = SessionStage.IDLE
    resume_step: str = "chatgpt_plan"
    latest_plan_text: str = ""
    plan_items: list[PlanItem] = Field(default_factory=list)
    messages: list[RelayMessage] = Field(default_factory=list)
    final_plan_markdown: str = ""
    executor_prompt: str = ""
    exported_files: list[str] = Field(default_factory=list)
    error: str | None = None
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class CreateSessionRequest(BaseModel):
    title: str
    user_request: str
    target_workspace: str = "."
    cdp_url: str = "http://127.0.0.1:9223"
    mode: SessionMode = SessionMode.LIVE_CDP
    max_rounds: int = 4
    verify_command: str = ""


class CdpTabInfo(BaseModel):
    id: str
    title: str
    url: str
    role: Literal["chatgpt", "claude", "other"]


class CdpStatusResponse(BaseModel):
    connected: bool
    cdp_url: str
    browser: str | None = None
    chatgpt_ready: bool = False
    claude_ready: bool = False
    tabs: list[CdpTabInfo] = Field(default_factory=list)
    error: str | None = None


class DeleteExternalChatsRequest(BaseModel):
    confirm: bool = False
    chat_ids: list[int] | None = None
