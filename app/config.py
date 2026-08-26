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


def _secret(name: str) -> str:
    """讀機密設定，並把貼錯的前後引號拿掉。

    在 Zeabur／Railway 的環境變數欄位貼上 "abc" 或 'abc' 是很常見的手誤，
    引號會變成授權碼的一部分，結果就是「明明設了卻一直說授權碼錯誤」。
    """
    raw = (os.getenv(name) or "").strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in {'"', "'"}:
        raw = raw[1:-1].strip()
    return raw


def _path(name: str, default: str) -> Path:
    raw = (os.getenv(name) or default).strip()
    p = Path(raw)
    return p if p.is_absolute() else (ROOT / p)


# ── 文字模型供應商（OpenAI 相容）──────────────────────────────
# 兩家都是 OpenAI 相容的 /chat/completions＋Bearer 認證，所以只差設定：
#   nvidia  —— NVIDIA Build（免費額度、開源模型）
#   zeabur  —— Zeabur AI Hub（一把金鑰通到 GPT／Claude／Gemini／Grok）
# 供應商用 LLM_PROVIDER 指定；沒指定時，設了 ZEABUR_API_KEY 就走 zeabur，
# 否則維持 nvidia（既有部署不受影響）。
_PROVIDER_PRESETS: dict[str, dict[str, object]] = {
    "nvidia": {
        "base_url": "https://integrate.api.nvidia.com/v1",
        "model": "meta/llama-3.1-70b-instruct",
        "fast": "meta/llama-3.1-8b-instruct",
        "strong": "meta/llama-3.3-70b-instruct",
        "known": [
            "meta/llama-3.1-70b-instruct",
            "meta/llama-3.3-70b-instruct",
            "nvidia/llama-3.3-nemotron-super-49b-v1.5",
            "moonshotai/kimi-k2-instruct",
            "qwen/qwen2.5-72b-instruct",
            "mistralai/mixtral-8x22b-instruct",
        ],
    },
    "zeabur": {
        # 東京節點離台灣最近；要換區域設 LLM_BASE_URL=https://sfo1.aihub.zeabur.ai/v1
        "base_url": "https://hnd1.aihub.zeabur.ai/v1",
        "model": "gpt-4o",
        "fast": "gpt-4o-mini",
        "strong": "claude-3-5-sonnet",
        "known": [
            "gpt-4o",
            "gpt-4o-mini",
            "claude-3-5-sonnet",
            "claude-3-opus",
            "gemini-2.0-flash",
            "grok-2",
        ],
    },
}

NVIDIA_API_KEY = _secret("NVIDIA_API_KEY")
ZEABUR_API_KEY = _secret("ZEABUR_API_KEY")


def _resolve_provider() -> str:
    explicit = (os.getenv("LLM_PROVIDER") or "").strip().lower()
    if explicit in _PROVIDER_PRESETS:
        return explicit
    return "zeabur" if ZEABUR_API_KEY else "nvidia"


LLM_PROVIDER = _resolve_provider()
_PRESET = _PROVIDER_PRESETS[LLM_PROVIDER]

# 金鑰：優先用供應商專屬變數，其次是通用的 LLM_API_KEY
LLM_API_KEY = (
    (ZEABUR_API_KEY if LLM_PROVIDER == "zeabur" else NVIDIA_API_KEY)
    or _secret("LLM_API_KEY")
)
LLM_BASE_URL = (
    os.getenv("LLM_BASE_URL")
    or os.getenv("NVIDIA_BASE_URL")
    or str(_PRESET["base_url"])
).rstrip("/")
LLM_MODEL = (os.getenv("LLM_MODEL") or os.getenv("NVIDIA_MODEL") or str(_PRESET["model"])).strip()
LLM_FAST_MODEL = (os.getenv("LLM_FAST_MODEL") or os.getenv("NVIDIA_FAST_MODEL") or str(_PRESET["fast"])).strip()
LLM_STRONG_MODEL = (
    os.getenv("LLM_STRONG_MODEL") or os.getenv("NVIDIA_STRONG_MODEL") or str(_PRESET["strong"])
).strip()

# 舊名保留：既有程式與 .env 都還在用 NVIDIA_* 這組名字。
# 它們現在只是「目前供應商」的別名，不再綁定 NVIDIA。
NVIDIA_BASE_URL = LLM_BASE_URL
NVIDIA_MODEL = LLM_MODEL
NVIDIA_FAST_MODEL = LLM_FAST_MODEL
NVIDIA_STRONG_MODEL = LLM_STRONG_MODEL
# NVIDIA Build 以額度計費；Zeabur AI Hub 是預付點數。要看美元估算就填單價。
NVIDIA_INPUT_COST_PER_MILLION = float(
    os.getenv("LLM_INPUT_COST_PER_MILLION") or os.getenv("NVIDIA_INPUT_COST_PER_MILLION") or 0
)
NVIDIA_OUTPUT_COST_PER_MILLION = float(
    os.getenv("LLM_OUTPUT_COST_PER_MILLION") or os.getenv("NVIDIA_OUTPUT_COST_PER_MILLION") or 0
)

# ── fal.ai 視覺服務（圖片輸入與輸出）──────────────────────────
# 圖片理解（使用者上傳的照片、海報）與視覺稿生成都走 fal.ai；
# 未設定時，文字工作台仍可正常使用，只是圖片功能會誠實說明未啟用。
FAL_KEY = (os.getenv("FAL_KEY") or "").strip()
FAL_VISION_MODEL = (os.getenv("FAL_VISION_MODEL") or "google/gemini-2.5-flash").strip()
FAL_IMAGE_MODEL = (os.getenv("FAL_IMAGE_MODEL") or "fal-ai/flux/schnell").strip()

# 給介面下拉選單用：目前供應商已知支援 function calling 的模型。
# 可用 LLM_KNOWN_MODELS（逗號分隔）覆寫。
KNOWN_TOOL_MODELS = [
    m.strip()
    for m in (os.getenv("LLM_KNOWN_MODELS") or ",".join(_PRESET["known"])).split(",")  # type: ignore[arg-type]
    if m.strip()
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

# ── 視覺資產庫 ────────────────────────────────────────────────
# 原圖、縮圖與非破壞式衍生圖都放在獨立目錄。部署時應與 DB_PATH
# 一起指向持久化磁碟；任何 API 都不提供刪除或覆寫原圖的能力。
VISUAL_ASSET_DIR = _path("VISUAL_ASSET_DIR", "data/visual-assets")
VISUAL_EXPORT_DIR = _path("VISUAL_EXPORT_DIR", "outputs/visual-exports")
VISUAL_MAX_FILE_BYTES = max(1_000_000, int(os.getenv("VISUAL_MAX_FILE_BYTES") or 20_000_000))
VISUAL_MAX_BATCH = max(1, min(100, int(os.getenv("VISUAL_MAX_BATCH") or 30)))
VISUAL_SEARCH_LIMIT = max(1, min(200, int(os.getenv("VISUAL_SEARCH_LIMIT") or 60)))

# ── InsForge 視覺資料層（可選、server-only）────────────────────
# 預設停用；本地 SQLite/檔案儲存仍是 source-of-truth。API key 絕不送到瀏覽器。
INSFORGE_BASE_URL = (os.getenv("INSFORGE_BASE_URL") or "").strip().rstrip("/")
INSFORGE_ANON_KEY = _secret("INSFORGE_ANON_KEY")
INSFORGE_SERVICE_KEY = _secret("INSFORGE_SERVICE_KEY")
INSFORGE_TIMEOUT_SECONDS = max(2.0, float(os.getenv("INSFORGE_TIMEOUT_SECONDS") or 15))
INSFORGE_STORAGE_BUCKET = (os.getenv("INSFORGE_STORAGE_BUCKET") or "visual-assets").strip()
INSFORGE_SEARCH_RPC = (os.getenv("INSFORGE_SEARCH_RPC") or "visual_hybrid_search").strip()
INSFORGE_FUNCTION_PREFIX = (os.getenv("INSFORGE_FUNCTION_PREFIX") or "functions").strip("/")
INSFORGE_TRUSTED = _bool("INSFORGE_TRUSTED", False)
INSFORGE_SYNC_MODE = (os.getenv("INSFORGE_SYNC_MODE") or "local").strip().lower()
INSFORGE_ALLOW_PRIVATE_SYNC = _bool("INSFORGE_ALLOW_PRIVATE_SYNC", False)
INSFORGE_OWNER_ID = (os.getenv("INSFORGE_OWNER_ID") or "").strip()
INSFORGE_ALLOW_ARTIFACT_FILE_SYNC = _bool("INSFORGE_ALLOW_ARTIFACT_FILE_SYNC", False)
INSFORGE_ALLOW_KNOWLEDGE_SYNC = _bool("INSFORGE_ALLOW_KNOWLEDGE_SYNC", False)
INSFORGE_ALLOW_CONVERSATION_KNOWLEDGE_SYNC = _bool("INSFORGE_ALLOW_CONVERSATION_KNOWLEDGE_SYNC", False)
INSFORGE_SYNC_LIMIT = max(1, min(5000, int(os.getenv("INSFORGE_SYNC_LIMIT") or 5000)))
INSFORGE_SYNC_WORKERS = max(1, min(12, int(os.getenv("INSFORGE_SYNC_WORKERS") or 6)))

# ── 認證 ─────────────────────────────────────────────────────
# local  = 單人本機模式，不需要登入，所有請求都是同一個使用者
# token  = 部署模式，要先用 APP_ACCESS_TOKEN 換到身分才能用
AUTH_MODE = (os.getenv("AUTH_MODE") or "").strip().lower()
APP_ACCESS_TOKEN = _secret("APP_ACCESS_TOKEN")
ADMIN_ACCESS_TOKEN = _secret("ADMIN_ACCESS_TOKEN")
ACCESS_CODE_COOKIE_NAME = (os.getenv("ACCESS_CODE_COOKIE_NAME") or "tku_zen_access").strip() or "tku_zen_access"
ACCESS_CODE_COOKIE_MAX_AGE = int(os.getenv("ACCESS_CODE_COOKIE_MAX_AGE") or 2592000)
ADMIN_COOKIE_MAX_AGE = int(os.getenv("ADMIN_COOKIE_MAX_AGE") or 43200)
ADMIN_COOKIE_NAME = "tku_zen_admin"

# Instagram is deliberately configuration-only in this phase.  No credential is
# ever returned to the browser or exposed to the model/tool registry.
INSTAGRAM_ACCESS_TOKEN = _secret("INSTAGRAM_ACCESS_TOKEN")
INSTAGRAM_BUSINESS_ACCOUNT_ID = (os.getenv("INSTAGRAM_BUSINESS_ACCOUNT_ID") or "").strip()
INSTAGRAM_APP_ID = (os.getenv("INSTAGRAM_APP_ID") or "").strip()

# 對外發佈總開關。預設關閉：不設定就永遠是草稿模式，
# 任何人（含管理者）都拿不到 can_approve / can_spend 權限。
EXTERNAL_PUBLISH_ENABLED = _bool("EXTERNAL_PUBLISH_ENABLED", False)

# ── API 限流 ──────────────────────────────────────────────────
# /api/chat 會呼叫外部模型（花額度），要有獨立且較嚴的限流。
CHAT_RATE_LIMIT = int(os.getenv("CHAT_RATE_LIMIT") or 12)          # 每視窗最多幾次
CHAT_RATE_WINDOW = int(os.getenv("CHAT_RATE_WINDOW") or 60)        # 視窗秒數
API_RATE_LIMIT = int(os.getenv("API_RATE_LIMIT") or 240)           # 一般 API 每視窗上限
API_RATE_WINDOW = int(os.getenv("API_RATE_WINDOW") or 60)

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
TOOL_TIMEOUT_SECONDS = float(os.getenv("TOOL_TIMEOUT_SECONDS") or 120)
TOOL_MAX_ATTEMPTS = max(1, int(os.getenv("TOOL_MAX_ATTEMPTS") or 2))

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
(ROOT / "data").mkdir(parents=True, exist_ok=True)
VISUAL_ASSET_DIR.mkdir(parents=True, exist_ok=True)
VISUAL_EXPORT_DIR.mkdir(parents=True, exist_ok=True)


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
    if not LLM_API_KEY or LLM_API_KEY.startswith(("nvapi-請填入", "請填入")):
        if LLM_PROVIDER == "zeabur":
            problems.append(
                "尚未設定 ZEABUR_API_KEY。請到 Zeabur 後台 → AI Hub → API Keys 取得，"
                "再填進 .env 或部署環境變數。"
            )
        else:
            problems.append(
                "尚未設定 NVIDIA_API_KEY。請到 https://build.nvidia.com/settings/api-keys "
                "免費申請，再填進專案根目錄的 .env 檔。"
            )
    elif LLM_PROVIDER == "nvidia" and not LLM_API_KEY.startswith("nvapi-"):
        problems.append("NVIDIA_API_KEY 格式看起來不對，正常應該以 nvapi- 開頭。")
    if not FAL_KEY:
        problems.append("尚未設定 FAL_KEY，圖片理解與視覺稿生成會停用（文字功能不受影響）。")
    if DEFAULT_DESTINATION not in {"local", "drive", "both"}:
        problems.append(f"DEFAULT_DESTINATION 只能是 local / drive / both，目前是 {DEFAULT_DESTINATION!r}。")
    if auth_mode() == "token" and not APP_ACCESS_TOKEN:
        problems.append("AUTH_MODE=token 但未設定 APP_ACCESS_TOKEN。部署前請補上授權碼。")
    return problems
