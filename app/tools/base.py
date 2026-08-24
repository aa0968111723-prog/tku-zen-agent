"""產出檔案的共用邏輯：檔名清理、落點（本機／雲端）、回報格式。"""

from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass, field
from pathlib import Path

from .. import config

_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
VALID_DESTINATIONS = {"local", "drive", "both"}


def safe_filename(name: str, default_ext: str) -> str:
    name = (name or "").strip() or "未命名"
    name = _ILLEGAL.sub("_", name).strip(" .")
    if not name:
        name = "未命名"
    if not name.lower().endswith(default_ext.lower()):
        name = f"{name}{default_ext}"
    return name[:180]


def dated_dir() -> Path:
    d = config.OUTPUT_DIR / _dt.date.today().isoformat()
    d.mkdir(parents=True, exist_ok=True)
    return d


def unique_path(directory: Path, filename: str) -> Path:
    p = directory / filename
    if not p.exists():
        return p
    stem, suffix = p.stem, p.suffix
    for i in range(2, 100):
        cand = directory / f"{stem}_{i}{suffix}"
        if not cand.exists():
            return cand
    return directory / f"{stem}_{_dt.datetime.now():%H%M%S}{suffix}"


@dataclass
class Artifact:
    """一份產出。工具回傳這個，agent 再轉成給模型看的文字。"""

    filename: str
    local_path: Path | None = None
    drive_url: str | None = None
    drive_error: str | None = None
    summary: str = ""
    details: list[str] = field(default_factory=list)
    artifact_id: str | None = None
    version: int = 1
    meta: dict = field(default_factory=dict)

    def to_result(self) -> dict:
        # 給模型看的訊息刻意不含伺服器絕對路徑：模型用不到，
        # 而且它常常會把路徑原封不動貼進回覆裡給使用者看。
        lines = [f"已完成：{self.filename}"]
        if self.summary:
            lines.append(self.summary)
        lines.extend(self.details)
        if self.version > 1:
            lines.append(f"（這是第 {self.version} 版，前一版仍保留）")
        if self.drive_url:
            lines.append(f"雲端連結：{self.drive_url}")
        if self.drive_error:
            lines.append(f"（雲端上傳失敗：{self.drive_error}　檔案仍已存在本機）")
        return {
            "ok": True,
            "message": "\n".join(lines),
            "filename": self.filename,
            "artifact_id": self.artifact_id,
            "version": self.version,
            "parent_artifact_id": self.meta.get("parent_artifact_id"),
            "activity_id": self.meta.get("activity_id"),
            # local_path 只在伺服器內部流轉（verification、download 用），
            # main.py 送到前端之前會拿掉。
            "local_path": str(self.local_path) if self.local_path else None,
            "drive_url": self.drive_url,
        }


def resolve_destination(requested: str | None) -> str:
    dest = (requested or config.DEFAULT_DESTINATION or "local").strip().lower()
    aliases = {
        "本機": "local", "local only": "local", "電腦": "local",
        "雲端": "drive", "google": "drive", "google drive": "drive", "雲端硬碟": "drive",
        "兩者": "both", "都要": "both", "all": "both",
    }
    dest = aliases.get(dest, dest)
    return dest if dest in VALID_DESTINATIONS else "local"


def deliver(path: Path, destination: str, artifact: Artifact, mime: str | None = None) -> Artifact:
    """依落點設定決定要不要上傳雲端，並登記成可追蹤、有歸屬的 artifact。

    檔案一律先寫在本機（雲端上傳需要來源檔）。
    """
    artifact.local_path = path
    dest = resolve_destination(destination)

    if dest in {"drive", "both"}:
        from . import drive as drive_tool  # 延後 import，沒裝 google 套件也不影響本機模式

        try:
            artifact.drive_url = drive_tool.upload(path, mime=mime)
        except Exception as exc:  # noqa: BLE001 —— 上傳失敗不該讓整個產出失敗
            artifact.drive_error = str(exc)

    if dest == "drive" and artifact.drive_url:
        artifact.details.append("（依設定只放雲端，但本機仍保留一份備份）")

    _register(artifact, path)
    return artifact


def _register(artifact: Artifact, path: Path) -> None:
    """登記到 artifact 表，讓前端只拿到 artifact_id 而不是伺服器路徑。

    沒有 RequestContext 時（單元測試直接呼叫工具）就跳過 ——
    產檔本身仍然成功，只是沒有歸屬記錄。
    """
    from ..services import context as ctx_mod

    ctx = ctx_mod.current()
    if ctx is None:
        return

    from ..services.session_store import get_store

    try:
        store = get_store()
        if ctx.project_id and not artifact.meta.get("activity_id"):
            active = store.recall(ctx.project_id).get("__active_activity_id__")
            if active and active.get("value"):
                artifact.meta["activity_id"] = active["value"]
        record = store.record_artifact(
            user_id=ctx.user_id,
            session_id=ctx.session_id,
            project_id=ctx.project_id,
            filename=path.name,
            kind=path.suffix.lstrip(".").lower(),
            local_path=str(path),
            drive_url=artifact.drive_url,
            meta=artifact.meta,
        )
    except Exception:  # noqa: BLE001 —— 登記失敗不該讓已經產好的檔案變成失敗
        return

    artifact.artifact_id = record.id
    artifact.version = record.version
    artifact.meta["parent_artifact_id"] = record.parent_id
