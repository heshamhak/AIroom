from __future__ import annotations

from contextlib import asynccontextmanager
import asyncio
from pathlib import Path
import shutil
import tempfile
from fastapi import Depends, FastAPI, HTTPException, Query, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from auth import TokenAuth
from cdp_bridge import (
    capture_tab_screenshot,
    ensure_chrome_cdp,
    inspect_cdp,
    interact_tab,
    launch_chrome_cdp,
)
from config import APP_VERSION, DEFAULT_ORIGIN_REGEX, load_settings
from executor import export_session_artifacts, redact_secrets
from models import (
    CreateSessionRequest,
    DeleteExternalChatsRequest,
    Session,
    SessionStage,
)
from orchestrator import SessionStore
from db import DatabaseManager

settings = load_settings()
store = SessionStore(db=DatabaseManager(settings.db_path))
auth = TokenAuth(token_path=settings.token_path)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await store.init()
    _ = auth.token
    yield
    await store.close()


app = FastAPI(title="AIroom Orchestrator API", version=APP_VERSION, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_origin_regex=DEFAULT_ORIGIN_REGEX,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type", "X-API-Token"],
)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "airoom-backend", "sessions": len(store.sessions)}


@app.get("/api/health/details", dependencies=[Depends(auth.require_http)])
async def health_details(cdp_url: str = "http://127.0.0.1:9223") -> dict:
    sqlite_ok = False
    try:
        async def _ping(conn):
            cur = await conn.execute("SELECT 1;")
            return (await cur.fetchone())[0] == 1
        sqlite_ok = await store.db.read(_ping)
    except Exception:
        sqlite_ok = False

    try:
        cdp = await asyncio.wait_for(inspect_cdp(cdp_url), timeout=2.0)
        cdp_info = {
            "connected": cdp.connected,
            "chatgpt_ready": cdp.chatgpt_ready,
            "claude_ready": cdp.claude_ready,
        }
    except Exception:
        cdp_info = {"connected": False, "chatgpt_ready": False, "claude_ready": False}

    disk = shutil.disk_usage(store.db.db_path.parent)
    return {
        "status": "ok" if sqlite_ok else "degraded",
        "sqlite": {"ok": sqlite_ok},
        "cdp": cdp_info,
        "disk": {
            "free_mb": disk.free // (1024 * 1024),
            "total_mb": disk.total // (1024 * 1024),
        },
        "sessions": len(store.sessions),
    }


@app.get("/api/cdp/status", dependencies=[Depends(auth.require_http)])
async def cdp_status(cdp_url: str = "http://127.0.0.1:9223"):
    return await inspect_cdp(cdp_url)


@app.post("/api/cdp/launch-chrome", dependencies=[Depends(auth.require_http)])
async def cdp_launch(port: int = 9223):
    return launch_chrome_cdp(port=port)


class BrowserInteractRequest(BaseModel):
    cdp_url: str = "http://127.0.0.1:9223"
    role: str = "chatgpt"
    action: str
    x: float | None = None
    y: float | None = None
    text: str | None = None
    key: str | None = None
    delta_y: float = 0
    url: str | None = None


@app.post("/api/cdp/ensure", dependencies=[Depends(auth.require_http)])
async def cdp_ensure(cdp_url: str = "http://127.0.0.1:9223"):
    return await ensure_chrome_cdp(cdp_url)


@app.get("/api/cdp/screenshot", dependencies=[Depends(auth.require_http)])
async def cdp_screenshot(
    role: str = Query(default="chatgpt", pattern="^(chatgpt|claude)$"),
    cdp_url: str = "http://127.0.0.1:9223",
):
    png = await capture_tab_screenshot(cdp_url, role)  # type: ignore[arg-type]
    return Response(content=png, media_type="image/png")


@app.post("/api/cdp/interact", dependencies=[Depends(auth.require_http)])
async def cdp_interact(req: BrowserInteractRequest):
    if req.role not in ("chatgpt", "claude"):
        raise HTTPException(status_code=400, detail="Invalid role")
    return await interact_tab(
        cdp_url=req.cdp_url,
        role=req.role,  # type: ignore[arg-type]
        action=req.action,
        x=req.x,
        y=req.y,
        text=req.text,
        key=req.key,
        delta_y=req.delta_y,
        url=req.url,
    )


@app.post("/api/sessions", response_model=Session, dependencies=[Depends(auth.require_http)])
async def create_session(req: CreateSessionRequest) -> Session:
    return await store.create_persisted(req)


@app.get("/api/sessions", response_model=list[Session], dependencies=[Depends(auth.require_http)])
async def list_sessions() -> list[Session]:
    return store.list_all()


@app.get("/api/sessions/{session_id}", response_model=Session, dependencies=[Depends(auth.require_http)])
async def get_session(session_id: str) -> Session:
    session = store.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


@app.post("/api/sessions/{session_id}/start", response_model=Session, dependencies=[Depends(auth.require_http)])
async def start_session(session_id: str) -> Session:
    session = store.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    await store.start(session_id)
    return session


@app.post("/api/sessions/{session_id}/pause", response_model=Session, dependencies=[Depends(auth.require_http)])
async def pause_session(session_id: str) -> Session:
    session = store.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    store.request_pause(session_id)
    return session


@app.post("/api/sessions/{session_id}/export", response_model=Session, dependencies=[Depends(auth.require_http)])
async def export_session(session_id: str) -> Session:
    session = store.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    plan_md, exec_prompt, files = export_session_artifacts(session)
    session.final_plan_markdown = plan_md
    session.executor_prompt = exec_prompt
    session.exported_files = files
    session.stage = SessionStage.EXPORTED
    await store.broadcast(session_id, "exported")
    return session


@app.post("/api/sessions/{session_id}/execute", dependencies=[Depends(auth.require_http)])
async def execute_session_handoff(session_id: str) -> dict:
    """Gate execution strictly behind consensus_reached/exported, record redacted handoff."""
    session = store.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.stage not in (SessionStage.CONSENSUS_REACHED, SessionStage.EXPORTED):
        raise HTTPException(
            status_code=409,
            detail="Execution blocked: consensus_reached or exported stage required.",
        )
    if not session.exported_files:
        plan_md, exec_prompt, files = export_session_artifacts(session)
        session.final_plan_markdown = plan_md
        session.executor_prompt = exec_prompt
        session.exported_files = files
        session.stage = SessionStage.EXPORTED
        await store.broadcast(session_id, "exported")

    safe_details = redact_secrets(
        f"Prepared Antigravity executor handoff with {len(session.exported_files)} files."
    )
    exec_id = await store.db.record_execution(session_id, "ready_for_executor", safe_details)
    return {
        "execution_id": exec_id,
        "status": "ready_for_executor",
        "exported_files": session.exported_files,
        "details": safe_details,
    }


@app.get("/api/sessions/{session_id}/executions", dependencies=[Depends(auth.require_http)])
async def list_session_executions(session_id: str) -> list[dict]:
    if not store.get(session_id):
        raise HTTPException(status_code=404, detail="Session not found")
    return await store.db.list_executions(session_id)


@app.get("/api/sessions/{session_id}/artifacts", dependencies=[Depends(auth.require_http)])
async def list_session_artifacts(session_id: str) -> list[dict]:
    session = store.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    airoom_dir = (Path(session.target_workspace) / ".airoom").resolve()
    if not airoom_dir.exists():
        return []
    files = []
    for p in sorted(airoom_dir.iterdir()):
        if p.is_file():
            files.append({"name": p.name, "size": p.stat().st_size})
    return files


@app.get("/api/sessions/{session_id}/artifacts/read", dependencies=[Depends(auth.require_http)])
async def read_session_artifact(session_id: str, filename: str = Query(...)) -> dict:
    session = store.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    airoom_dir = (Path(session.target_workspace) / ".airoom").resolve()
    target = (airoom_dir / filename).resolve()
    if not target.is_relative_to(airoom_dir) or target == airoom_dir:
        raise HTTPException(status_code=403, detail="Path traversal blocked")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="Artifact not found")
    return {"name": target.name, "content": target.read_text(encoding="utf-8")}


@app.get("/api/sessions/{session_id}/external-chats", dependencies=[Depends(auth.require_http)])
async def list_external_chats(session_id: str) -> list[dict]:
    if not store.get(session_id):
        raise HTTPException(status_code=404, detail="Session not found")
    chats = await store.db.list_external_chats(session_id)
    # Whitelist only AIroom-created chats
    return [c for c in chats if c.get("created_by_airoom") == 1]


@app.post("/api/sessions/{session_id}/external-chats/delete", dependencies=[Depends(auth.require_http)])
async def delete_external_chats(session_id: str, req: DeleteExternalChatsRequest) -> dict:
    """Phase 1: Atomically mark previewed AIroom-created chats for cleanup & audit; requires explicit chat_ids."""
    if not store.get(session_id):
        raise HTTPException(status_code=404, detail="Session not found")
    chats = await store.db.list_external_chats(session_id)
    candidates = [
        c
        for c in chats
        if c.get("created_by_airoom") == 1 and c.get("deletion_status") == "active"
    ]
    if not req.confirm:
        return {"confirmed": False, "candidates": candidates}
    if not req.chat_ids:
        raise HTTPException(
            status_code=400,
            detail="Explicit chat_ids from preview are required when confirm=true.",
        )

    outcome = await store.db.mark_external_chats_cleanup_atomic(session_id, req.chat_ids)
    return {"confirmed": True, **outcome}


@app.post("/api/db/backup", dependencies=[Depends(auth.require_http)])
async def backup_db() -> dict:
    backup_path = await store.db.backup_database(store.db.db_path.parent / "backups")
    with tempfile.TemporaryDirectory() as tmp_dir:
        ok = store.db.verify_restore_isolated(
            backup_path, tmp_dir, source_db=store.db.db_path
        )
    return {"backup_path": str(backup_path), "restore_verified": ok}


@app.websocket("/ws/sessions/{session_id}")
async def session_ws(
    websocket: WebSocket,
    session_id: str,
    last_seq: int | None = Query(default=None),
    token: str | None = Query(default=None),
) -> None:
    if not auth.verify_ws(websocket, token):
        await websocket.close(code=1008)
        return
    await store.subscribe(session_id, websocket, last_seq=last_seq)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        store.unsubscribe(session_id, websocket)
