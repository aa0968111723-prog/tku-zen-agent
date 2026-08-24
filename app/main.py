"""FastAPI application with router-level general/admin authorization."""

from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import json
import logging
import threading
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, Response
from fastapi.openapi.utils import get_openapi
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config, orchestrator, retrieval
from .services import audit as audit_service
from .services import auth, context as ctx_mod
from .services import activities as activity_service
from .services import current_term as term_service
from .services import fal as fal_service
from .services import memory as memory_service
from .services import permissions, ratelimit
from .services.session_store import get_store
from .orchestrator.state import Stage, WorkflowStatus

logger = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"

app = FastAPI(
    title="淡江大學領袖禪學社 AI 工作台",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,  # 預設路由關閉；下方以權限控管的自訂路由取代
)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


# ── 安全標頭與 CSRF Origin 檢查 ────────────────────────────────

_CSP = (
    "default-src 'self'; "
    "img-src 'self' data: https:; "  # fal 視覺稿與雲端縮圖是外部 https 圖片
    "style-src 'self'; "
    "script-src 'self'; "
    "connect-src 'self'; "
    "font-src 'self'; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'none'"
)

_STATE_CHANGING = {"POST", "PUT", "PATCH", "DELETE"}


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    # CSRF 縱深防禦：瀏覽器跨站請求一定帶 Origin，比對不上就擋。
    # 沒有 Origin 的請求（curl、同站舊瀏覽器 GET）不在此攔——
    # cookie 的 SameSite 已經擋掉跨站自動帶 cookie 的情況。
    if request.method in _STATE_CHANGING:
        origin = request.headers.get("origin")
        if origin and origin.lower() != "null":
            origin_host = urlsplit(origin).netloc
            if origin_host and origin_host != request.headers.get("host", ""):
                return JSONResponse(
                    {"detail": "來源網域不符，請從工作台頁面操作"}, status_code=403
                )

    # 一般 API 限流（每 IP）。/api/chat 另有更嚴的每人限流。
    if request.url.path.startswith("/api/"):
        ip = request.client.host if request.client else "unknown"
        ok, retry_after = ratelimit.allow(
            f"api:{ip}", config.API_RATE_LIMIT, config.API_RATE_WINDOW
        )
        if not ok:
            return JSONResponse(
                {"detail": "請求太頻繁，請稍後再試"},
                status_code=429,
                headers={"Retry-After": str(retry_after)},
            )

    response = await call_next(request)

    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault(
        "Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()"
    )
    response.headers.setdefault("Content-Security-Policy", _CSP)
    if config.auth_mode() == "token":
        # 部署模式一定走 https（cookie 也標 Secure），可以放心開 HSTS
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
        )
    return response


# ── OpenAPI（部署模式僅限管理者）──────────────────────────────

def _build_openapi() -> dict[str, Any]:
    schema = get_openapi(title=app.title, version="3.0.0", routes=app.routes)
    components = schema.setdefault("components", {})
    components["securitySchemes"] = {
        "sessionCookie": {
            "type": "apiKey",
            "in": "cookie",
            "name": config.ACCESS_CODE_COOKIE_NAME,
            "description": "透過 POST /api/auth 以授權碼登入後核發的身分 cookie。",
        },
        "adminCookie": {
            "type": "apiKey",
            "in": "cookie",
            "name": config.ADMIN_COOKIE_NAME,
            "description": "透過 POST /api/admin/auth 以管理碼登入後核發的管理 cookie。",
        },
    }
    schema["security"] = [{"sessionCookie": []}]
    admin_prefixes = ("/api/admin", "/api/reindex", "/api/instagram/publish",
                      "/api/instagram/comments", "/api/instagram/messages")
    for path, ops in schema.get("paths", {}).items():
        if path.startswith(admin_prefixes) or path == "/api/term":
            for op in ops.values():
                if isinstance(op, dict):
                    op["security"] = [{"sessionCookie": [], "adminCookie": []}]
    return schema


@app.get("/openapi.json", include_in_schema=False)
async def openapi_json(request: Request) -> JSONResponse:
    if config.auth_mode() == "token":
        permissions.require_manage(request)  # 部署模式：管理者才能看 API 規格
    return JSONResponse(_build_openapi())


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
    # 結構化研究欄位（政大事故後新增）：前端表單明確指定研究模式與對象時，
    # 不再只靠一句 prose 讓後端猜。全部可選，舊前端不帶也完全相容。
    research_mode: str | None = Field(default=None, pattern=r"^(internal|external|comparative)$")
    research_school: str | None = Field(default=None, max_length=40)
    research_entity_id: str | None = Field(default=None, max_length=80)


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
    perms = permissions.resolve(request)
    return {
        "mode": config.auth_mode(),
        "authenticated": perms.user_id is not None,
        "is_admin": perms.can_manage,
        "permissions": {
            "can_view": perms.can_view,
            "can_manage": perms.can_manage,
            "can_approve": perms.can_approve,
            "can_spend": perms.can_spend,
        },
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


# 續接時預設還原的訊息數（約 3–5 輪對話）
RESUME_RECENT_MESSAGES = 10
RESUME_FULL_MESSAGES = 100


def _visible_messages(raw: list[dict[str, Any]]) -> list[dict[str, str]]:
    """只留使用者看得懂的對話（user / 有內容的 assistant），不含工具往返。"""
    out: list[dict[str, str]] = []
    for m in raw:
        role = m.get("role")
        text = (m.get("content") or "").strip()
        if role == "user" and text:
            out.append({"role": "user", "text": text})
        elif role == "assistant" and text and not m.get("tool_calls"):
            out.append({"role": "assistant", "text": text})
    return out


@general_router.get("/session/resume")
async def resume_session(
    session_id: str, full: bool = False, user_id: str = Depends(current_user)
) -> dict[str, Any]:
    """回傳續接一個工作階段所需的完整脈絡。

    找不到（過期、被刪、不是自己的）一律 404，前端顯示明確訊息。
    """
    store = get_store()
    sess = store.get_session(session_id, user_id)
    if not sess:
        raise HTTPException(status_code=404, detail="這個工作階段已過期或不存在，請建立新任務")

    raw = store.load_messages(session_id)
    visible = _visible_messages(raw)
    limit = RESUME_FULL_MESSAGES if full else RESUME_RECENT_MESSAGES
    recent = visible[-limit:]

    project_id = sess.get("project_id")
    task_type = ""
    task_label = ""
    pending_steps: list[str] = []
    completion_status = ""
    summary = ""
    artifacts: list[dict[str, Any]] = []
    if project_id:
        project = store.get_project(project_id, user_id) or {}
        task_type = project.get("task_type") or ""
        state = memory_service.load_state(store, project_id)
        if state is not None:
            task_type = state.task_type.value or task_type
            pending_steps = [s.description for s in state.plan_steps if not s.done]
            completion_status = state.completion_status
        raw_summary = store.recall(project_id).get(memory_service.SUMMARY_KEY)
        summary = raw_summary["value"] if raw_summary else ""
        artifacts = [a.public() for a in store.list_artifacts(user_id, project_id=project_id, limit=10)]

    from .skills import SKILL_BY_NAME

    for s in SKILL_BY_NAME.values():
        if s.task_type == task_type:
            task_label = s.label
            break

    return {
        "session_id": session_id,
        "title": sess.get("title") or "",
        "updated_at": sess.get("updated_at"),
        "task_type": task_type,
        "task_label": task_label,
        "messages": recent,
        "message_count": len(visible),
        "truncated": len(visible) > len(recent),
        "pending_steps": pending_steps,
        "completion_status": completion_status,
        "summary": summary,
        "artifacts": artifacts,
        "project_id": project_id,
    }


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


# ── 執行中任務登記（取消 + 同 session 防併發）─────────────────

class _TurnRegistry:
    """每個 session 同時只允許一輪任務；取消時設旗標讓串流即時收尾。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events: dict[str, asyncio.Event] = {}

    def start(self, session_id: str) -> asyncio.Event | None:
        with self._lock:
            if session_id in self._events:
                return None
            ev = asyncio.Event()
            self._events[session_id] = ev
            return ev

    def cancel(self, session_id: str) -> bool:
        with self._lock:
            ev = self._events.get(session_id)
        if ev is None:
            return False
        ev.set()
        return True

    def finish(self, session_id: str) -> None:
        with self._lock:
            self._events.pop(session_id, None)


TURNS = _TurnRegistry()


def _sse(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


class CancelRequest(BaseModel):
    session_id: str


@general_router.post("/chat")
async def chat(req: ChatRequest, user_id: str = Depends(current_user)) -> StreamingResponse:
    ok, retry_after = ratelimit.allow(
        f"chat:{user_id}", config.CHAT_RATE_LIMIT, config.CHAT_RATE_WINDOW
    )
    if not ok:
        raise HTTPException(
            status_code=429,
            detail="訊息傳送太頻繁，請稍後再試",
            headers={"Retry-After": str(retry_after)},
        )

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

    cancel_event = TURNS.start(session_id)
    if cancel_event is None:
        raise HTTPException(status_code=409, detail="這個工作階段已有正在執行的任務，請先停止或稍候")

    async def stream():
        yield _sse({"type": "session", "session_id": session_id})
        requested = {
            k: v
            for k, v in {
                "mode": req.research_mode,
                "school": req.research_school,
                "entity_id": req.research_entity_id,
            }.items()
            if v
        }
        agen = orchestrator.run_turn(
            ctx_mod.RequestContext(user_id=user_id, session_id=session_id),
            req.message,
            destination=req.destination,
            model=req.model,
            attachments=attachments,
            requested=requested or None,
        )
        cancel_wait = asyncio.create_task(cancel_event.wait())
        try:
            while True:
                next_event = asyncio.create_task(agen.__anext__())
                done, _pending = await asyncio.wait(
                    {next_event, cancel_wait}, return_when=asyncio.FIRST_COMPLETED
                )
                if cancel_wait in done and next_event not in done:
                    # 使用者按了停止：中斷正在等的模型呼叫、關閉產生器
                    next_event.cancel()
                    with contextlib.suppress(BaseException):
                        await next_event
                    with contextlib.suppress(BaseException):
                        await agen.aclose()
                    yield _sse({"type": "cancelled", "text": "已停止生成"})
                    break
                try:
                    event = next_event.result()
                except StopAsyncIteration:
                    break
                yield _sse(event)
                if cancel_event.is_set():
                    with contextlib.suppress(BaseException):
                        await agen.aclose()
                    yield _sse({"type": "cancelled", "text": "已停止生成"})
                    break
        except Exception:
            logger.exception("chat stream failed")
            yield _sse({"type": "error", "text": "系統忙碌中，請稍後再試"})
        finally:
            cancel_wait.cancel()
            with contextlib.suppress(BaseException):
                await agen.aclose()
            TURNS.finish(session_id)
        yield 'data: {"type": "done"}\n\n'

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@general_router.post("/chat/cancel")
async def chat_cancel(req: CancelRequest, request: Request, user_id: str = Depends(current_user)) -> dict[str, Any]:
    """停止這個 session 正在執行的生成，並釋放 session 執行鎖。

    任務層級的暫停／取消（會改 workflow 狀態）走 /api/tasks/{project_id}/*；
    這裡只負責把「正在跑的這一輪」停下來。
    """
    store = get_store()
    if not store.get_session(req.session_id, user_id):
        raise HTTPException(status_code=404, detail="找不到這個工作階段")
    cancelled = TURNS.cancel(req.session_id)
    audit_service.write_audit(
        action="chat.cancel",
        actor_user_id=user_id,
        detail={"note": "已送出停止訊號" if cancelled else "目前沒有執行中的任務"},
        request=request,
    )
    return {"ok": True, "cancelled": cancelled}


@general_router.get("/instagram/status")
async def instagram_status() -> dict[str, Any]:
    connected = bool(config.INSTAGRAM_ACCESS_TOKEN and config.INSTAGRAM_BUSINESS_ACCOUNT_ID)
    publish_allowed = connected and config.EXTERNAL_PUBLISH_ENABLED
    return {
        "connected": connected,
        "mode": "connected" if publish_allowed else "draft",
        "publish_enabled": publish_allowed,
    }


@admin_router.get("/admin/audit")
async def admin_audit(limit: int = 50) -> dict[str, Any]:
    return {"events": get_store().list_audit(min(max(limit, 1), 200))}


class InstagramActionRequest(BaseModel):
    """對外發佈類操作的共同請求格式。confirm 沒有明確為 true 一律拒絕。"""

    confirm: bool = False


def _instagram_write_gate(request: Request, req: InstagramActionRequest, action: str) -> None:
    """對外發佈的統一守門：登入 → 管理 → 核准權 → 花費權 → 明確確認 → 能力檢查。

    每一步都 fail closed；全部通過後才會碰到「功能尚未啟用」。
    所有嘗試（不論成敗）都寫入稽核。這些動作同時列在
    roles.FORBIDDEN_AUTONOMOUS_ACTIONS —— 代理永遠不能自主執行。
    """
    actor = auth.optional_identity(request)
    try:
        permissions.require_publish(request)
        permissions.require_confirmation(req.confirm)
    except HTTPException as exc:
        audit_service.write_audit(
            action=f"instagram.{action}",
            actor_user_id=actor,
            detail={"note": str(exc.detail)},
            request=request,
            ok=False,
        )
        raise
    audit_service.write_audit(
        action=f"instagram.{action}",
        actor_user_id=actor,
        detail={"note": "權限檢查通過，功能尚未啟用"},
        request=request,
    )
    if not (config.INSTAGRAM_ACCESS_TOKEN and config.INSTAGRAM_BUSINESS_ACCOUNT_ID):
        raise HTTPException(status_code=501, detail="尚未連接 Instagram 官方 API，目前為草稿模式")
    raise HTTPException(status_code=501, detail="Instagram 發布功能尚未啟用")


@app.post("/api/instagram/publish")
async def instagram_publish(req: InstagramActionRequest, request: Request) -> None:
    _instagram_write_gate(request, req, "publish")


@app.post("/api/instagram/comments/reply")
async def instagram_reply(req: InstagramActionRequest, request: Request) -> None:
    _instagram_write_gate(request, req, "reply")


@app.post("/api/instagram/messages/send")
async def instagram_send(req: InstagramActionRequest, request: Request) -> None:
    _instagram_write_gate(request, req, "send")


app.include_router(general_router)
app.include_router(admin_router)
