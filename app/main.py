"""FastAPI application with router-level general/admin authorization."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config, orchestrator, retrieval
from .services import auth, context as ctx_mod
from .services import current_term as term_service
from .services import memory as memory_service
from .services.session_store import get_store
from .orchestrator.state import Stage, WorkflowStatus

logger = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"

app = FastAPI(title="淡江大學領袖禪學社 AI 工作台", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


class ChatRequest(BaseModel):
    session_id: str | None = None
    message: str = Field(min_length=1, max_length=8000)
    destination: str = config.DEFAULT_DESTINATION
    model: str | None = None


class SessionRequest(BaseModel):
    session_id: str | None = None


class LoginRequest(BaseModel):
    # Empty/malformed values must reach the auth layer and receive the same 401.
    token: str | None = Field(default=None, max_length=200)


class TermRequest(BaseModel):
    fields: dict[str, Any]


general_router = APIRouter(prefix="/api", dependencies=[Depends(auth.require_user)])
admin_router = APIRouter(prefix="/api", dependencies=[Depends(auth.require_admin)])


def current_user(request: Request, response: Response) -> str:
    return auth.require_user(request, response)


@app.post("/api/auth")
async def login(req: LoginRequest, request: Request, response: Response) -> dict[str, bool]:
    auth.login(request, response, req.token)
    return {"ok": True}


@app.get("/api/auth")
async def auth_status(request: Request) -> dict[str, Any]:
    return {
        "mode": config.auth_mode(),
        "authenticated": auth.optional_identity(request) is not None,
        "is_admin": auth.admin_identity(request),
    }


@app.post("/api/admin/auth")
async def admin_login(req: LoginRequest, request: Request, response: Response) -> dict[str, bool]:
    auth.admin_login(request, response, req.token)
    return {"ok": True}


@app.post("/api/auth/logout")
async def logout(response: Response) -> dict[str, bool]:
    auth.logout(response)
    return {"ok": True}


@app.post("/api/admin/logout")
async def admin_logout(request: Request, response: Response) -> dict[str, bool]:
    auth.admin_logout(request, response)
    return {"ok": True}


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    return HTMLResponse((STATIC / "index.html").read_text(encoding="utf-8"))


@general_router.get("/health")
async def health(_user_id: str = Depends(current_user)) -> dict[str, Any]:
    return orchestrator.health(slim=True)


@admin_router.get("/admin/health")
async def admin_health() -> dict[str, Any]:
    return orchestrator.health()


def _resolve_session(user_id: str, session_id: str | None) -> str:
    store = get_store()
    if session_id and store.get_session(session_id, user_id):
        return session_id
    return store.create_session(user_id)


@general_router.post("/session")
async def ensure_session(req: SessionRequest, user_id: str = Depends(current_user)) -> dict[str, Any]:
    sid = _resolve_session(user_id, req.session_id)
    store = get_store()
    sess = store.get_session(sid, user_id) or {}
    return {"session_id": sid, "project_id": sess.get("project_id"), "message_count": store.message_count(sid)}


@general_router.get("/sessions")
async def list_sessions(user_id: str = Depends(current_user)) -> dict[str, Any]:
    store = get_store()
    return {
        "sessions": [
            {
                "session_id": s["id"],
                "title": s["title"],
                "project_id": s["project_id"],
                "updated_at": s["updated_at"],
            }
            for s in store.list_sessions(user_id)
        ]
    }


@general_router.post("/reset")
async def reset(req: SessionRequest, user_id: str = Depends(current_user)) -> dict[str, bool]:
    if not req.session_id:
        return {"ok": True, "cleared": False}
    store = get_store()
    if not store.get_session(req.session_id, user_id):
        raise HTTPException(status_code=404, detail="找不到這個工作階段")
    store.clear_messages(req.session_id)
    return {"ok": True, "cleared": True}


@admin_router.post("/reindex")
async def reindex() -> dict[str, Any]:
    index = await asyncio.to_thread(retrieval.get_index, rebuild=True)
    return index.stats()


@general_router.get("/term")
async def get_term() -> dict[str, Any]:
    return term_service.as_form()


@admin_router.post("/term")
async def save_term(req: TermRequest) -> dict[str, Any]:
    term_service.update(req.fields)
    return term_service.as_form()


@general_router.get("/artifacts")
async def list_artifacts(
    project_id: str | None = None, limit: int = 30, user_id: str = Depends(current_user)
) -> dict[str, Any]:
    items = get_store().list_artifacts(user_id, project_id=project_id, limit=min(limit, 100))
    return {"artifacts": [a.public() for a in items]}


def _task_state(user_id: str, project_id: str):
    store = get_store()
    if not store.get_project(project_id, user_id):
        raise HTTPException(status_code=404, detail="找不到這個任務")
    state = memory_service.load_state(store, project_id)
    if state is None:
        raise HTTPException(status_code=404, detail="這個任務目前沒有可恢復的工作流")
    return store, state


def _task_payload(project_id: str, state, *, action: str = "") -> dict[str, Any]:
    payload = state.public_summary()
    payload.update({
        "project_id": project_id,
        "action": action,
        "steps": [
            {
                "step_id": s.step_id,
                "description": s.description,
                "status": s.status,
                "done": s.done,
                "attempts": s.attempts,
                "error": s.last_error,
                "note": s.note,
                "depends_on": s.depends_on,
            }
            for s in state.plan_steps
        ],
    })
    return payload


@general_router.get("/tasks/{project_id}")
async def task_status(project_id: str, user_id: str = Depends(current_user)) -> dict[str, Any]:
    _store, state = _task_state(user_id, project_id)
    return _task_payload(project_id, state)


async def _control_task(project_id: str, action: str, user_id: str) -> dict[str, Any]:
    store, state = _task_state(user_id, project_id)
    if action == "pause":
        if state.workflow_status in {WorkflowStatus.COMPLETED, WorkflowStatus.CANCELLED}:
            raise HTTPException(status_code=409, detail="已結束的任務不能暫停")
        state.workflow_status = WorkflowStatus.PAUSED
        state.next_action = "按繼續，或在對話中說「接續剛才」"
    elif action == "resume":
        if state.workflow_status == WorkflowStatus.CANCELLED:
            raise HTTPException(status_code=409, detail="已取消的任務請使用重新執行")
        state.workflow_status = WorkflowStatus.IN_PROGRESS
        state.completion_status = "in_progress"
        state.next_action = state.next_step().description if state.next_step() else ""
    elif action == "retry":
        count = state.reset_failed_steps()
        if not count:
            raise HTTPException(status_code=409, detail="目前沒有可重試的失敗步驟")
        state.next_action = "只重試失敗步驟"
    elif action == "cancel":
        if state.workflow_status == WorkflowStatus.COMPLETED:
            raise HTTPException(status_code=409, detail="已完成的任務不能取消")
        state.workflow_status = WorkflowStatus.CANCELLED
        state.completion_status = "failed"
        state.stage = Stage.FAILED
        state.next_action = "如要再做，請說「重新執行這個任務」"
    else:
        raise HTTPException(status_code=400, detail="不支援的任務操作")
    memory_service.save_state(store, project_id, state)
    return _task_payload(project_id, state, action=action)


@general_router.post("/tasks/{project_id}/pause")
async def pause_task(project_id: str, user_id: str = Depends(current_user)) -> dict[str, Any]:
    return await _control_task(project_id, "pause", user_id)


@general_router.post("/tasks/{project_id}/resume")
async def resume_task(project_id: str, user_id: str = Depends(current_user)) -> dict[str, Any]:
    return await _control_task(project_id, "resume", user_id)


@general_router.post("/tasks/{project_id}/retry")
async def retry_task(project_id: str, user_id: str = Depends(current_user)) -> dict[str, Any]:
    return await _control_task(project_id, "retry", user_id)


@general_router.post("/tasks/{project_id}/cancel")
async def cancel_task(project_id: str, user_id: str = Depends(current_user)) -> dict[str, Any]:
    return await _control_task(project_id, "cancel", user_id)


@general_router.get("/download")
async def download(artifact_id: str, user_id: str = Depends(current_user)) -> FileResponse:
    record = get_store().get_artifact(artifact_id, user_id)
    if record is None:
        raise HTTPException(status_code=404, detail="找不到這份產出")
    target = Path(record.local_path).resolve()
    root = config.OUTPUT_DIR.resolve()
    if not target.is_relative_to(root) or not target.is_file():
        raise HTTPException(status_code=404, detail="這份產出的檔案已不存在")
    return FileResponse(target, filename=record.filename)


@general_router.post("/chat")
async def chat(req: ChatRequest, user_id: str = Depends(current_user)) -> StreamingResponse:
    session_id = _resolve_session(user_id, req.session_id)

    async def stream():
        yield f'data: {json.dumps({"type": "session", "session_id": session_id}, ensure_ascii=False)}\n\n'
        try:
            async for event in orchestrator.run_turn(
                ctx_mod.RequestContext(user_id=user_id, session_id=session_id),
                req.message,
                destination=req.destination,
                model=req.model,
            ):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except Exception:  # noqa: BLE001
            logger.exception("chat stream failed")
            payload = {"type": "error", "text": "系統忙碌中，請稍後再試"}
            yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
        yield 'data: {"type": "done"}\n\n'

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@general_router.get("/instagram/status")
async def instagram_status() -> dict[str, Any]:
    connected = bool(config.INSTAGRAM_ACCESS_TOKEN and config.INSTAGRAM_BUSINESS_ACCOUNT_ID)
    return {"connected": connected, "mode": "connected" if connected else "draft"}


async def _instagram_write() -> None:
    if not (config.INSTAGRAM_ACCESS_TOKEN and config.INSTAGRAM_BUSINESS_ACCOUNT_ID):
        raise HTTPException(status_code=501, detail="尚未連接 Instagram 官方 API，目前為草稿模式")
    raise HTTPException(status_code=501, detail="Instagram 發布功能尚未啟用")


@admin_router.post("/instagram/publish")
async def instagram_publish() -> None:
    await _instagram_write()


@admin_router.post("/instagram/comments/reply")
async def instagram_reply() -> None:
    await _instagram_write()


@admin_router.post("/instagram/messages/send")
async def instagram_send() -> None:
    await _instagram_write()


app.include_router(general_router)
app.include_router(admin_router)
