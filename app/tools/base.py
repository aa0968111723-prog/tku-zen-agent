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

    def to_result(self) -> dict:
        lines = [f"已完成：{self.filename}"]
        if self.summary:
            lines.append(self.summary)
        lines.extend(self.details)
        if self.local_path:
            lines.append(f"本機位置：{self.local_path}")
        if self.drive_url:
            lines.append(f"雲端連結：{self.drive_url}")
        if self.drive_error:
            lines.append(f"（雲端上傳失敗：{self.drive_error}　檔案仍已存在本機）")
        return {
            "ok": True,
            "message": "\n".join(lines),
            "filename": self.filename,
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
    """依落點設定決定要不要上傳雲端。檔案一律先寫在本機（雲端上傳需要來源檔）。"""
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

    return artifact
