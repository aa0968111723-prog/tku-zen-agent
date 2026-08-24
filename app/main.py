"""FastAPI application with router-level general/admin authorization."""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config, orchestrator, retrieval
from .services import audit as audit_service
from .services import auth, context as ctx_mod
from .services import activities as activity_service
from .services import current_term as term_service
from .services import fal as fal_service
from .services import memory as memory_service
from .services.session_store import get_store
from .orchestrator.state import Stage, WorkflowStatus

logger = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"

app = FastAPI(title="淡江大學領袖禪學社 AI 工作台", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


class ImageAttachment(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    media_type: str = Field(pattern=r"^image/(jpeg|png|webp)$")
    data_url: str = Field(min_length=32, max_length=3_000_000)


class ChatRequest(BaseModel):
    session_id: str | None = None
    message: str = Field(min_length=1, max_length=8000)
    destination: str = config.DEFAULT_DESTINATION
    model: str | None = None
    attachments: list[ImageAttachment] = Field(default_factory=list, max_length=2)


class VisualGenerateRequest(BaseModel):
    """使用者從產出卡片主動要求的 fal 視覺稿。"""

    prompt: str = Field(min_length=4, max_length=7000)


class SessionRequest(BaseModel):
    session_id: str | None = None


class LoginRequest(BaseModel):
    # Empty/malformed values must reach the auth layer and receive the same 401.
    token: str | None = Field(default=None, max_length=200)


class TermRequest(BaseModel):
    fields: dict[str, Any]


class ActivityCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    project_id: str | None = None
    activity_type: str = ""
    academic_year: str = ""
    semester: str = ""
    status: str = "planning"
    start_at: str = ""
    end_at: str = ""
    location: str = ""
    goal: str = ""
    audience: str = ""
    signup_url: str = ""
    owner: str = ""
    notes: str = ""


class ActivityUpdateRequest(BaseModel):
    name: str | None = None
    project_id: str | None = None
    activity_type: str | None = None
    academic_year: str | None = None
    semester: str | None = None
    status: str | None = None
    start_at: str | None = None
    end_at: str | None = None
    location: str | None = None
    goal: str | None = None
    audience: str | None = None
    signup_url: str | None = None
    owner: str | None = None
    notes: str | None = None


class ActivityTaskCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    group_name: str = ""
    assignee: str = ""
    due_at: str = ""
    status: str = "pending"
    priority: str = "normal"
    notes: str = ""


class ActivityTaskUpdateRequest(BaseModel):
    title: str | None = None
    group_name: str | None = None
    assignee: str | None = None
    due_at: str | None = None
    status: str | None = None
    priority: str | None = None
    notes: str | None = None


general_router = APIRouter(prefix="/api", dependencies=[Depends(auth.require_user)])
admin_router = APIRouter(prefix="/api", dependencies=[Depends(auth.require_admin)])


def current_user(request: Request, response: Response) -> str:
    return auth.require_user(request, response)


@app.post("/api/auth")
async def login(req: LoginRequest, request: Request, response: Response) -> dict[str, bool]:
    try:
        auth.login(request, response, req.token)
    except HTTPException as exc:
        audit_service.write_audit(
            action="auth.login",
            detail={"status": exc.status_code},
            request=request,
            ok=False,
        )
        raise
    audit_service.write_audit(
        action="auth.login",
        actor_user_id=auth.optional_identity(request),
        detail={"status": 200},
        request=request,
    )
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
    try:
        auth.admin_login(request, response, req.token)
    except HTTPException as exc:
        audit_service.write_audit(
            action="auth.admin_login",
            detail={"status": exc.status_code},
            request=request,
            ok=False,
        )
        raise
    audit_service.write_audit(
        action="auth.admin_login",
        actor_user_id=auth.optional_identity(request),
        detail={"status": 200},
        request=request,
    )
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
async def reindex(request: Request) -> dict[str, Any]:
    try:
        index = await asyncio.to_thread(retrieval.get_index, rebuild=True)
        stats = index.stats()
    except Exception as exc:  # noqa: BLE001
        audit_service.write_audit(
            action="admin.reindex",
            actor_user_id=auth.optional_identity(request),
            detail={"error": str(exc)},
            request=request,
            ok=False,
        )
        raise
    audit_service.write_audit(
        action="admin.reindex",
        actor_user_id=auth.optional_identity(request),
        detail=stats,
        request=request,
    )
    return stats


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


def _activity_payload(store, user_id: str, activity: dict[str, Any], output_type: str = "status") -> dict[str, Any]:
    tasks = store.list_activity_tasks(activity["id"], user_id)
    report = activity_service.readiness_report(activity, tasks, output_type=output_type)
    return {
        **activity_service.public_activity(activity),
        "tasks": tasks,
        "readiness": {key: value for key, value in report.items() if key != "activity"},
    }


@general_router.get("/activities")
async def list_activities(
    status: str = "", semester: str = "", activity_type: str = "", limit: int = 50,
    user_id: str = Depends(current_user),
) -> dict[str, Any]:
    try:
        normalized = activity_service.normalize_activity_status(status) if status else ""
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    records = get_store().list_activities(
        user_id, status=normalized, semester=semester, activity_type=activity_type, limit=limit,
    )
    return {"activities": [activity_service.public_activity(item) for item in records]}


@general_router.post("/activities")
async def create_activity(req: ActivityCreateRequest, user_id: str = Depends(current_user)) -> dict[str, Any]:
    try:
        values = req.model_dump()
        values["status"] = activity_service.normalize_activity_status(values["status"])
        values["start_at"] = activity_service.validate_temporal(values["start_at"], "活動開始時間")
        values["end_at"] = activity_service.validate_temporal(values["end_at"], "活動結束時間")
        name = values.pop("name")
        project_id = values.pop("project_id")
        term = term_service.load().known()
        values["academic_year"] = values["academic_year"] or term.get("academic_year", "")
        values["semester"] = values["semester"] or term.get("semester", "")
        activity = get_store().create_activity(user_id, name, project_id=project_id, **values)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _activity_payload(get_store(), user_id, activity)


@general_router.get("/activities/{activity_id}")
async def get_activity(
    activity_id: str, output_type: str = "status", user_id: str = Depends(current_user),
) -> dict[str, Any]:
    store = get_store()
    activity = store.get_activity(activity_id, user_id)
    if not activity:
        raise HTTPException(status_code=404, detail="找不到這場活動")
    return _activity_payload(store, user_id, activity, output_type=output_type)


@general_router.patch("/activities/{activity_id}")
async def update_activity(
    activity_id: str, req: ActivityUpdateRequest, user_id: str = Depends(current_user),
) -> dict[str, Any]:
    fields = req.model_dump(exclude_unset=True)
    try:
        if "status" in fields:
            fields["status"] = activity_service.normalize_activity_status(fields["status"] or "")
        if "start_at" in fields:
            fields["start_at"] = activity_service.validate_temporal(fields["start_at"] or "", "活動開始時間")
        if "end_at" in fields:
            fields["end_at"] = activity_service.validate_temporal(fields["end_at"] or "", "活動結束時間")
        activity = get_store().update_activity(activity_id, user_id, fields)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not activity:
        raise HTTPException(status_code=404, detail="找不到這場活動")
    return _activity_payload(get_store(), user_id, activity)


@general_router.post("/activities/{activity_id}/tasks")
async def create_activity_task(
    activity_id: str, req: ActivityTaskCreateRequest, user_id: str = Depends(current_user),
) -> dict[str, Any]:
    try:
        values = req.model_dump()
        values["due_at"] = activity_service.validate_temporal(values["due_at"], "工作期限")
        values["status"] = activity_service.normalize_task_status(values["status"])
        values["priority"] = activity_service.normalize_priority(values["priority"])
        title = values.pop("title")
        task = get_store().create_activity_task(user_id, activity_id, title, **values)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return task


@general_router.patch("/activities/{activity_id}/tasks/{task_id}")
async def update_activity_task(
    activity_id: str, task_id: str, req: ActivityTaskUpdateRequest,
    user_id: str = Depends(current_user),
) -> dict[str, Any]:
    store = get_store()
    current = store.get_activity_task(task_id, user_id)
    if not current or current["activity_id"] != activity_id:
        raise HTTPException(status_code=404, detail="找不到這項工作")
    fields = req.model_dump(exclude_unset=True)
    try:
        if "due_at" in fields:
            fields["due_at"] = activity_service.validate_temporal(fields["due_at"] or "", "工作期限")
        if "status" in fields:
            fields["status"] = activity_service.normalize_task_status(fields["status"] or "")
        if "priority" in fields:
            fields["priority"] = activity_service.normalize_priority(fields["priority"] or "")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return store.update_activity_task(task_id, user_id, fields) or current


@general_router.get("/activities/{activity_id}/artifacts")
async def activity_artifacts(activity_id: str, user_id: str = Depends(current_user)) -> dict[str, Any]:
    store = get_store()
    if not store.get_activity(activity_id, user_id):
        raise HTTPException(status_code=404, detail="找不到這場活動")
    artifacts = [
        item.public() for item in store.list_artifacts(user_id, limit=100)
        if (item.meta or {}).get("activity_id") == activity_id
    ]
    return {"artifacts": artifacts}


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


@general_router.post("/visual/generate")
async def generate_visual(req: VisualGenerateRequest, user_id: str = Depends(current_user)) -> dict[str, Any]:
    """產生一次性視覺稿，不保存提示詞或 fal 回應到任務資料庫。"""
    del user_id  # router 的授權依賴已確認身分；此端點沒有持久化資料。
    try:
        images = await fal_service.generate_image(req.prompt)
    except fal_service.FalError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
    return {"images": images}


@general_router.post("/chat")
async def chat(req: ChatRequest, user_id: str = Depends(current_user)) -> StreamingResponse:
    session_id = _resolve_session(user_id, req.session_id)
    attachments: list[dict[str, str]] = []
    for item in req.attachments:
        prefix = f"data:{item.media_type};base64,"
        if not item.data_url.startswith(prefix):
            raise HTTPException(status_code=422, detail="圖片格式不正確，請重新選取 JPG、PNG 或 WebP 圖片")
        try:
            raw = base64.b64decode(item.data_url[len(prefix):], validate=True)
        except (binascii.Error, ValueError):
            raise HTTPException(status_code=422, detail="圖片內容無法讀取，請重新選取") from None
        if not raw or len(raw) > 2_000_000:
            raise HTTPException(status_code=422, detail="每張圖片需小於 2MB，請壓縮後再加入")
        attachments.append({"name": item.name, "media_type": item.media_type, "data_url": item.data_url})

    async def stream():
        yield f'data: {json.dumps({"type": "session", "session_id": session_id}, ensure_ascii=False)}\n\n'
        try:
            async for event in orchestrator.run_turn(
                ctx_mod.RequestContext(user_id=user_id, session_id=session_id),
                req.message,
                destination=req.destination,
                model=req.model,
                attachments=attachments,
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
