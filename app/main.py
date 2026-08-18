"""FastAPI 伺服器。本機跑 `python -m app` 或部署到 Zeabur 都是同一份程式。

安全要點：
  · 每個請求都要先識別 user（本機模式自動、部署模式要存取碼）
  · session / project / artifact 一律驗歸屬，猜到別人的 id 也讀不到
  · 前端只拿得到 artifact_id，拿不到伺服器上的絕對路徑
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config, orchestrator, retrieval
from .services import auth, context as ctx_mod
from .services import current_term as term_service
from .services.session_store import get_store

STATIC = Path(__file__).parent / "static"

app = FastAPI(title="淡江大學領袖禪學社 · AI 代理", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


# ── 請求模型 ──────────────────────────────────────────────────

class ChatRequest(BaseModel):
    session_id: str | None = None
    message: str = Field(min_length=1, max_length=8000)
    destination: str = config.DEFAULT_DESTINATION
    model: str | None = None


class SessionRequest(BaseModel):
    session_id: str | None = None


class LoginRequest(BaseModel):
    token: str = Field(min_length=1, max_length=200)


class TermRequest(BaseModel):
    fields: dict[str, Any]


# ── 身分 ─────────────────────────────────────────────────────

def current_user(request: Request, response: Response) -> str:
    return auth.identify(request, response)


@app.post("/api/auth")
async def login(req: LoginRequest, response: Response) -> dict[str, Any]:
    user_id = auth.login(response, req.token)
    return {"ok": True, "user_id": user_id}


@app.get("/api/auth")
async def auth_status(request: Request) -> dict[str, Any]:
    return {
        "mode": config.auth_mode(),
        "authenticated": auth.optional_identity(request) is not None,
    }


# ── 頁面 ─────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    return HTMLResponse((STATIC / "index.html").read_text(encoding="utf-8"))


@app.get("/api/health")
async def health() -> dict[str, Any]:
    return orchestrator.health()


# ── session ──────────────────────────────────────────────────

def _resolve_session(user_id: str, session_id: str | None) -> str:
    """取得（或建立）這個使用者的 session。

    別人的 session_id 在這裡會被當成不存在，直接開一個新的 ——
    不回報「這個 id 存在但不是你的」，避免變成存在性探測。
    """
    store = get_store()
    if session_id and store.get_session(session_id, user_id):
        return session_id
    return store.create_session(user_id)


@app.post("/api/session")
async def ensure_session(req: SessionRequest, user_id: str = Depends(current_user)) -> dict[str, Any]:
    sid = _resolve_session(user_id, req.session_id)
    store = get_store()
    sess = store.get_session(sid, user_id) or {}
    return {
        "session_id": sid,
        "project_id": sess.get("project_id"),
        "message_count": store.message_count(sid),
    }


@app.get("/api/sessions")
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


@app.post("/api/reset")
async def reset(req: SessionRequest, user_id: str = Depends(current_user)) -> dict[str, Any]:
    """只清得掉自己的 session。"""
    if not req.session_id:
        return {"ok": True, "cleared": False}
    store = get_store()
    if not store.get_session(req.session_id, user_id):
        raise HTTPException(status_code=404, detail="找不到這個對話。")
    store.clear_messages(req.session_id)
    return {"ok": True, "cleared": True}


# ── 知識庫 ────────────────────────────────────────────────────

@app.post("/api/reindex")
async def reindex(_user_id: str = Depends(current_user)) -> dict[str, Any]:
    return retrieval.get_index(rebuild=True).stats()


# ── 本學期設定 ────────────────────────────────────────────────

@app.get("/api/term")
async def get_term(_user_id: str = Depends(current_user)) -> dict[str, Any]:
    return term_service.as_form()


@app.post("/api/term")
async def save_term(req: TermRequest, _user_id: str = Depends(current_user)) -> dict[str, Any]:
    term_service.update(req.fields)
    return term_service.as_form()


# ── 產出 ─────────────────────────────────────────────────────

@app.get("/api/artifacts")
async def list_artifacts(
    project_id: str | None = None, limit: int = 30, user_id: str = Depends(current_user)
) -> dict[str, Any]:
    items = get_store().list_artifacts(user_id, project_id=project_id, limit=min(limit, 100))
    return {"artifacts": [a.public() for a in items]}


@app.get("/api/download")
async def download(artifact_id: str, user_id: str = Depends(current_user)) -> FileResponse:
    """只能下載自己的產出。

    以前這個端點吃的是 ?path=，靠「必須在 outputs 底下」來擋 ——
    部署成多人使用之後那完全不夠，任何人都能下載別人的檔案。
    """
    record = get_store().get_artifact(artifact_id, user_id)
    if record is None:
        raise HTTPException(status_code=404, detail="找不到這份產出。")

    target = Path(record.local_path).resolve()
    root = config.OUTPUT_DIR.resolve()
    if not target.is_relative_to(root) or not target.is_file():
        raise HTTPException(status_code=404, detail="這份產出的檔案已經不在伺服器上了。")
    return FileResponse(target, filename=record.filename)


# ── 對話 ─────────────────────────────────────────────────────

@app.post("/api/chat")
async def chat(req: ChatRequest, user_id: str = Depends(current_user)) -> StreamingResponse:
    session_id = _resolve_session(user_id, req.session_id)

    async def stream():
        # 先把 session_id 告訴前端 —— 第一次對話時它還不知道
        yield f'data: {json.dumps({"type": "session", "session_id": session_id}, ensure_ascii=False)}\n\n'
        try:
            async for event in orchestrator.run_turn(
                ctx_mod.RequestContext(user_id=user_id, session_id=session_id),
                req.message,
                destination=req.destination,
                model=req.model,
            ):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except Exception as exc:  # noqa: BLE001
            payload = {"type": "error", "text": f"伺服器錯誤：{type(exc).__name__}: {exc}"}
            yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
        yield 'data: {"type": "done"}\n\n'

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
