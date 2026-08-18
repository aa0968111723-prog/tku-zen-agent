"""測試共用夾具。

核心原則：**所有測試都不能依賴 NVIDIA API 金鑰**。
需要模型回應的地方一律用 FakeLLM 腳本化，這樣 CI 與離線環境都跑得動。
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import config  # noqa: E402


@pytest.fixture(scope="session")
def project_root() -> Path:
    return ROOT


@pytest.fixture()
def tmp_output_dir(monkeypatch: pytest.MonkeyPatch):
    """把產出導到暫存資料夾，測試不會弄髒使用者的 outputs/。"""
    d = Path(tempfile.mkdtemp(prefix="tku_out_"))
    monkeypatch.setattr(config, "OUTPUT_DIR", d)
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture()
def tmp_db(monkeypatch: pytest.MonkeyPatch):
    """每個測試一個乾淨的 SQLite。"""
    from app.services import session_store

    d = Path(tempfile.mkdtemp(prefix="tku_db_"))
    path = d / "test.sqlite3"
    monkeypatch.setattr(config, "DB_PATH", path)
    store = session_store.SessionStore(path)
    monkeypatch.setattr(session_store, "_store", store)
    yield store
    store.close()
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture()
def index():
    """共用的檢索索引（建一次就好，2400 段大約 0.7 秒）。"""
    from app import retrieval

    return retrieval.get_index()


@pytest.fixture()
def clean_term(monkeypatch: pytest.MonkeyPatch):
    """把當期狀態導到暫存檔，測試不會動到真的 current_term.yaml。"""
    from app.services import current_term

    d = Path(tempfile.mkdtemp(prefix="tku_term_"))
    path = d / "current_term.yaml"
    monkeypatch.setattr(config, "CURRENT_TERM_FILE", path)
    current_term.invalidate()
    yield path
    current_term.invalidate()
    shutil.rmtree(d, ignore_errors=True)
