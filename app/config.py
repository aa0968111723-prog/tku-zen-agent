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

# ── 持久化 ───────────────────────────────────────────────────
# session / project / artifact 都存這裡。原本只在 process memory，
# 伺服器一重開就消失，多 process 部署還會互相看不到。
DB_PATH = _path("DB_PATH", "data/agent.sqlite3")

# ── 認證 ─────────────────────────────────────────────────────
# local  = 單人本機模式，不需要登入，所有請求都是同一個使用者
# token  = 部署模式，要先用 APP_ACCESS_TOKEN 換到身分才能用
AUTH_MODE = (os.getenv("AUTH_MODE") or "").strip().lower()
APP_ACCESS_TOKEN = (os.getenv("APP_ACCESS_TOKEN") or "").strip()
ADMIN_ACCESS_TOKEN = (os.getenv("ADMIN_ACCESS_TOKEN") or "").strip()
ACCESS_CODE_COOKIE_NAME = (os.getenv("ACCESS_CODE_COOKIE_NAME") or "tku_zen_access").strip() or "tku_zen_access"
ACCESS_CODE_COOKIE_MAX_AGE = int(os.getenv("ACCESS_CODE_COOKIE_MAX_AGE") or 2592000)
ADMIN_COOKIE_MAX_AGE = int(os.getenv("ADMIN_COOKIE_MAX_AGE") or 43200)
ADMIN_COOKIE_NAME = "tku_zen_admin"

# Instagram is deliberately configuration-only in this phase.  No credential is
# ever returned to the browser or exposed to the model/tool registry.
INSTAGRAM_ACCESS_TOKEN = (os.getenv("INSTAGRAM_ACCESS_TOKEN") or "").strip()
INSTAGRAM_BUSINESS_ACCOUNT_ID = (os.getenv("INSTAGRAM_BUSINESS_ACCOUNT_ID") or "").strip()
INSTAGRAM_APP_ID = (os.getenv("INSTAGRAM_APP_ID") or "").strip()

# ── 當期狀態（本學期的真實資料）────────────────────────────────
CURRENT_TERM_FILE = _path("CURRENT_TERM_FILE", "data/current_term.yaml")
EXTERNAL_REFERENCE_DIR = KNOWLEDGE_DIR / "社群"


def auth_mode() -> str:
    """沒明講的話：設了 APP_ACCESS_TOKEN 就是部署模式，否則本機單人。"""
    if AUTH_MODE in {"local", "token"}:
        return AUTH_MODE
    return "token" if APP_ACCESS_TOKEN else "local"

# ── 伺服器 ────────────────────────────────────────────────────
HOST = (os.getenv("HOST") or "127.0.0.1").strip()
PORT = int(os.getenv("PORT") or 8848)

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
(ROOT / "data").mkdir(parents=True, exist_ok=True)


def bootstrap_current_term() -> None:
    """第一次啟動時，從範本生一份可以編輯的本學期設定。

    真實的 current_term.yaml 會填上社長與幹部姓名，屬於個資，所以不進版控；
    進版控的是不含真實資料的 .example.yaml。
    """
    target = CURRENT_TERM_FILE
    example = ROOT / "data" / "current_term.example.yaml"
    if target.exists() or not example.exists():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")


bootstrap_current_term()


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
    if auth_mode() == "token" and not APP_ACCESS_TOKEN:
        problems.append("AUTH_MODE=token 但未設定 APP_ACCESS_TOKEN。部署前請補上授權碼。")
    return problems
