"""fal.ai 視覺服務的離線契約測試：不會真的傳送圖片或消耗額度。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import config
from app.services import fal


@pytest.mark.asyncio
async def test_describe_images_uses_fal_output_without_exposing_input(monkeypatch):
    seen: dict[str, object] = {}

    async def fake_post(path, payload):
        seen["path"] = path
        seen["payload"] = payload
        return {"output": "可見主題是新生茶會，日期未顯示。"}

    monkeypatch.setattr(fal, "_post_json", fake_post)
    summary = await fal.describe_images([
        {"name": "海報.png", "media_type": "image/png", "data_url": "data:image/png;base64,AA=="}
    ])

    assert summary == "可見主題是新生茶會，日期未顯示。"
    assert seen["path"] == "https://fal.run/openrouter/router/vision"
    assert seen["payload"]["image_urls"] == ["data:image/png;base64,AA=="]
    assert seen["payload"]["model"] == config.FAL_VISION_MODEL


@pytest.mark.asyncio
async def test_generate_image_returns_only_safe_https_urls(monkeypatch):
    async def fake_post(_path, _payload):
        return {
            "images": [
                {"url": "https://fal.media/files/visual.jpg", "content_type": "image/jpeg", "width": 1024, "height": 1024},
                {"url": "javascript:alert(1)"},
            ]
        }

    monkeypatch.setattr(fal, "_post_json", fake_post)
    assert await fal.generate_image("淡江社團招生視覺") == [{
        "url": "https://fal.media/files/visual.jpg", "content_type": "image/jpeg", "width": 1024, "height": 1024,
    }]


def test_fal_without_key_gives_a_safe_actionable_message(monkeypatch):
    monkeypatch.setattr(config, "FAL_KEY", "")
    with pytest.raises(fal.FalError, match="圖片理解尚未啟用") as exc:
        fal._headers()
    assert exc.value.code == "vision_not_configured"


def test_visual_generation_endpoint_returns_images(tmp_db, tmp_output_dir, monkeypatch):
    from app import main

    async def fake_generate(_prompt):
        return [{"url": "https://fal.media/files/visual.jpg", "content_type": "image/jpeg", "width": 1024, "height": 1024}]

    monkeypatch.setattr(main.fal_service, "generate_image", fake_generate)
    with TestClient(main.app) as client:
        response = client.post("/api/visual/generate", json={"prompt": "幫我做一張招生視覺稿"})
    assert response.status_code == 200
    assert response.json()["images"][0]["url"].startswith("https://")
