"""文字模型供應商切換（Zeabur AI Hub／NVIDIA Build）與 fal.ai 視覺服務。

兩家都是 OpenAI 相容端點，切換只該是設定問題；這裡確保：
  · 預設推斷正確（有 ZEABUR_API_KEY 就走 zeabur）
  · 舊的 NVIDIA_* 環境變數仍然有效（既有部署不會壞）
  · 錯誤訊息會指向**目前**供應商，不再硬編碼 NVIDIA
  · 圖片的輸入（理解）與輸出（生成）都走 fal.ai
"""

from __future__ import annotations

import importlib
import sys

import pytest


def _reload_config(monkeypatch, **env) -> tuple:
    """用指定環境變數重新載入 config 與 llm。"""
    import app.config  # noqa: F401  —— 確保模組已載入
    import app.llm  # noqa: F401
    for key in (
        "LLM_PROVIDER", "LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL",
        "LLM_FAST_MODEL", "LLM_STRONG_MODEL", "LLM_KNOWN_MODELS",
        "ZEABUR_API_KEY", "NVIDIA_API_KEY", "NVIDIA_BASE_URL", "NVIDIA_MODEL",
    ):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    config = importlib.reload(sys.modules["app.config"])
    llm = importlib.reload(sys.modules["app.llm"])
    return config, llm


@pytest.fixture(autouse=True)
def _restore_modules():
    """測試結束後把模組還原成真實環境的設定，不影響其他測試。"""
    import app.config  # noqa: F401
    import app.llm  # noqa: F401

    yield
    importlib.reload(sys.modules["app.config"])
    importlib.reload(sys.modules["app.llm"])


def test_zeabur_key_selects_zeabur_provider(monkeypatch):
    config, llm = _reload_config(monkeypatch, ZEABUR_API_KEY="zb-test")
    assert config.LLM_PROVIDER == "zeabur"
    assert config.LLM_BASE_URL == "https://hnd1.aihub.zeabur.ai/v1"
    assert config.LLM_MODEL == "gpt-4o"
    assert "claude-3-5-sonnet" in config.KNOWN_TOOL_MODELS
    assert llm.NvidiaClient().api_key == "zb-test"


def test_nvidia_key_keeps_nvidia_provider(monkeypatch):
    config, llm = _reload_config(monkeypatch, NVIDIA_API_KEY="nvapi-test")
    assert config.LLM_PROVIDER == "nvidia"
    assert config.LLM_BASE_URL.startswith("https://integrate.api.nvidia.com")
    assert config.LLM_MODEL.startswith("meta/llama")
    assert llm.NvidiaClient().api_key == "nvapi-test"


def test_explicit_provider_wins_over_inference(monkeypatch):
    config, _llm = _reload_config(
        monkeypatch, LLM_PROVIDER="nvidia", ZEABUR_API_KEY="zb-test", NVIDIA_API_KEY="nvapi-test",
    )
    assert config.LLM_PROVIDER == "nvidia"


def test_legacy_nvidia_env_still_overrides(monkeypatch):
    """既有部署用 NVIDIA_BASE_URL／NVIDIA_MODEL，升級後不能失效。"""
    config, _llm = _reload_config(
        monkeypatch,
        NVIDIA_API_KEY="nvapi-test",
        NVIDIA_BASE_URL="https://example.invalid/v1",
        NVIDIA_MODEL="custom/model",
    )
    assert config.LLM_BASE_URL == "https://example.invalid/v1"
    assert config.LLM_MODEL == "custom/model"
    assert config.NVIDIA_MODEL == config.LLM_MODEL  # 舊名是別名


def test_explicit_settings_override_preset(monkeypatch):
    config, _llm = _reload_config(
        monkeypatch,
        ZEABUR_API_KEY="zb-test",
        LLM_BASE_URL="https://sfo1.aihub.zeabur.ai/v1",
        LLM_MODEL="claude-3-5-sonnet",
        LLM_KNOWN_MODELS="claude-3-5-sonnet, gpt-4o",
    )
    assert config.LLM_BASE_URL == "https://sfo1.aihub.zeabur.ai/v1"
    assert config.LLM_MODEL == "claude-3-5-sonnet"
    assert config.KNOWN_TOOL_MODELS == ["claude-3-5-sonnet", "gpt-4o"]


def test_missing_key_message_names_current_provider(monkeypatch):
    _config, llm = _reload_config(monkeypatch, LLM_PROVIDER="zeabur")
    message = llm._missing_key_message()
    assert "Zeabur AI Hub" in message and "ZEABUR_API_KEY" in message
    assert "NVIDIA" not in message

    _config, llm = _reload_config(monkeypatch, LLM_PROVIDER="nvidia")
    message = llm._missing_key_message()
    assert "NVIDIA" in message and "NVIDIA_API_KEY" in message


def test_missing_config_reports_right_provider(monkeypatch):
    config, _llm = _reload_config(monkeypatch, LLM_PROVIDER="zeabur")
    problems = " ".join(config.missing_config())
    assert "ZEABUR_API_KEY" in problems

    config, _llm = _reload_config(monkeypatch, LLM_PROVIDER="nvidia")
    problems = " ".join(config.missing_config())
    assert "NVIDIA_API_KEY" in problems


def test_missing_fal_key_is_reported_but_not_fatal(monkeypatch):
    config, _llm = _reload_config(monkeypatch, ZEABUR_API_KEY="zb-test")
    monkeypatch.setattr(config, "FAL_KEY", "")
    problems = config.missing_config()
    assert any("FAL_KEY" in p for p in problems)
    assert all("已停止" not in p for p in problems), "圖片未設定不該讓服務無法啟動"


@pytest.mark.asyncio
async def test_zeabur_request_uses_bearer_and_chat_completions(monkeypatch):
    """Zeabur AI Hub 與 NVIDIA 一樣是 Bearer＋/chat/completions。"""
    config, llm = _reload_config(monkeypatch, ZEABUR_API_KEY="zb-test")
    seen: dict = {}

    async def fake_client():
        class _Resp:
            status_code = 200

            def json(self):
                return {"choices": [{"message": {"content": "好"}}]}

        class _Client:
            async def post(self, url, headers=None, json=None):  # noqa: A002
                seen["url"] = url
                seen["auth"] = (headers or {}).get("Authorization")
                seen["model"] = (json or {}).get("model")
                return _Resp()

        return _Client()

    monkeypatch.setattr(llm, "get_http_client", fake_client)
    reply = await llm.NvidiaClient().chat([{"role": "user", "content": "hi"}])
    assert reply.content == "好"
    assert seen["url"] == "https://hnd1.aihub.zeabur.ai/v1/chat/completions"
    assert seen["auth"] == "Bearer zb-test"
    assert seen["model"] == "gpt-4o"


def test_fal_handles_both_image_directions():
    """fal.ai 同時負責圖片輸入（理解）與輸出（生成）。"""
    from app.services import fal

    assert callable(fal.describe_images), "圖片輸入：理解上傳的照片"
    assert callable(fal.generate_image), "圖片輸出：生成視覺稿"


def test_frontend_model_labels_cover_zeabur_models():
    from pathlib import Path

    app_js = (Path(__file__).resolve().parent.parent / "app" / "static" / "app.js").read_text(encoding="utf-8")
    for pattern in ("gpt-4o", "claude", "gemini", "grok"):
        assert pattern in app_js.lower(), f"模型下拉缺少 {pattern} 的中文顯示名"
