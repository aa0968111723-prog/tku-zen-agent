"""集中管理設定。所有讀檔一律指定 encoding='utf-8'（Windows 預設是 cp950，會亂碼）。"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent

load_dotenv(ROOT / ".env", encoding="utf-8")


def _bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "是"}


def _path(name: str, default: str) -> Path:
    raw = (os.getenv(name) or default).strip()
    p = Path(raw)
    return p if p.is_absolute() else (ROOT / p)


# ── NVIDIA Build（OpenAI 相容）─────────────────────────────────
NVIDIA_API_KEY = (os.getenv("NVIDIA_API_KEY") or "").strip()
NVIDIA_BASE_URL = (os.getenv("NVIDIA_BASE_URL") or "https://integrate.api.nvidia.com/v1").rstrip("/")
NVIDIA_MODEL = (os.getenv("NVIDIA_MODEL") or "meta/llama-3.1-70b-instruct").strip()

# 給介面下拉選單用：已知支援 function calling 的免費模型
KNOWN_TOOL_MODELS = [
    "meta/llama-3.1-70b-instruct",
    "meta/llama-3.3-70b-instruct",
    "nvidia/llama-3.3-nemotron-super-49b-v1.5",
    "moonshotai/kimi-k2-instruct",
    "qwen/qwen2.5-72b-instruct",
    "mistralai/mixtral-8x22b-instruct",
]

# ── 產出落點 ──────────────────────────────────────────────────
DEFAULT_DESTINATION = (os.getenv("DEFAULT_DESTINATION") or "local").strip().lower()
OUTPUT_DIR = _path("OUTPUT_DIR", "outputs")

# ── Google 雲端硬碟 ───────────────────────────────────────────
DRIVE_FOLDER_ID = (os.getenv("DRIVE_FOLDER_ID") or "").strip()
GOOGLE_CREDENTIALS_FILE = _path("GOOGLE_CREDENTIALS_FILE", "data/google_credentials.json")
GOOGLE_TOKEN_FILE = _path("GOOGLE_TOKEN_FILE", "data/google_token.json")

# ── 知識庫 ────────────────────────────────────────────────────
KNOWLEDGE_DIR = ROOT / "knowledge"
PLAYBOOK_DIR = KNOWLEDGE_DIR / "劇本"
CORPUS_DIR = KNOWLEDGE_DIR / "語料"
DRIVE_DOCS_DIR = KNOWLEDGE_DIR / "雲端文件"
ENABLE_LINE_CORPUS = _bool("ENABLE_LINE_CORPUS", False)

# ── 伺服器 ────────────────────────────────────────────────────
HOST = (os.getenv("HOST") or "127.0.0.1").strip()
PORT = int(os.getenv("PORT") or 8848)

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
(ROOT / "data").mkdir(parents=True, exist_ok=True)


def missing_config() -> list[str]:
    """回傳啟動時該提醒使用者的設定問題。"""
    problems: list[str] = []
    if not NVIDIA_API_KEY or NVIDIA_API_KEY.startswith("nvapi-請填入"):
        problems.append(
            "尚未設定 NVIDIA_API_KEY。請到 https://build.nvidia.com/settings/api-keys "
            "免費申請，再填進專案根目錄的 .env 檔。"
        )
    elif not NVIDIA_API_KEY.startswith("nvapi-"):
        problems.append("NVIDIA_API_KEY 格式看起來不對，正常應該以 nvapi- 開頭。")
    if DEFAULT_DESTINATION not in {"local", "drive", "both"}:
        problems.append(f"DEFAULT_DESTINATION 只能是 local / drive / both，目前是 {DEFAULT_DESTINATION!r}。")
    return problems
