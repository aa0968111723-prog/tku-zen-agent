"""FastAPI 伺服器。本機跑 `python -m app` 或部署到 Zeabur 都是同一份程式。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import agent, config, retrieval

STATIC = Path(__file__).parent / "static"

app = FastAPI(title="淡江大學領袖禪學社 · AI 代理", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


class ChatRequest(BaseModel):
    session_id: str = "default"
    message: str
    destination: str = config.DEFAULT_DESTINATION
    model: str | None = None


class ResetRequest(BaseModel):
    session_id: str = "default"


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    return HTMLResponse((STATIC / "index.html").read_text(encoding="utf-8"))


@app.get("/api/health")
async def health() -> dict[str, Any]:
    return agent.health()


@app.post("/api/reset")
async def reset(req: ResetRequest) -> dict[str, bool]:
    agent.reset_session(req.session_id)
    return {"ok": True}


@app.post("/api/reindex")
async def reindex() -> dict[str, Any]:
    return retrieval.get_index(rebuild=True).stats()


@app.get("/api/outputs")
async def outputs(limit: int = 30) -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    if config.OUTPUT_DIR.exists():
        for p in sorted(
            (f for f in config.OUTPUT_DIR.rglob("*") if f.is_file()),
            key=lambda f: f.stat().st_mtime,
            reverse=True,
        )[:limit]:
            files.append(
                {
                    "filename": p.name,
                    "local_path": str(p),
                    "size": p.stat().st_size,
                    "modified": p.stat().st_mtime,
                }
            )
    return {"files": files}


@app.get("/api/download")
async def download(path: str) -> FileResponse:
    target = Path(path).resolve()
    root = config.OUTPUT_DIR.resolve()
    # 只准下載 outputs 底下的檔案
    if not target.is_relative_to(root) or not target.is_file():
        raise HTTPException(status_code=404, detail="找不到這個檔案，或它不在產出資料夾裡。")
    return FileResponse(target, filename=target.name)


@app.post("/api/chat")
async def chat(req: ChatRequest) -> StreamingResponse:
    async def stream():
        try:
            async for event in agent.run_turn(
                req.session_id,
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
