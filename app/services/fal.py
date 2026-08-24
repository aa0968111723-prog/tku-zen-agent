"""fal.ai 視覺服務。

文字代理仍走既有 NVIDIA/OpenAI 相容路徑；這個模組只處理圖片理解與使用者
主動要求的視覺稿生成。圖片 data URL 不寫入資料庫，也不記錄在 log。
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from .. import config

logger = logging.getLogger(__name__)


class FalError(RuntimeError):
    """可安全顯示給使用者的 fal 服務錯誤。"""

    def __init__(self, text: str, *, code: str = "vision_unavailable") -> None:
        super().__init__(text)
        self.code = code


def _headers() -> dict[str, str]:
    if not config.FAL_KEY:
        raise FalError(
            "圖片理解尚未啟用。請改以文字描述圖片內容，或請管理員設定視覺服務。",
            code="vision_not_configured",
        )
    return {"Authorization": f"Key {config.FAL_KEY}", "Accept": "application/json"}


async def _post_json(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    """呼叫 fal REST API；不將 request payload（可能含圖片）寫入日誌。"""
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(90.0, connect=15.0)) as client:
            response = await client.post(path, headers=_headers(), json=payload)
    except httpx.TimeoutException as exc:
        raise FalError("視覺服務回應逾時，請稍後重試或改以文字描述。", code="vision_timeout") from exc
    except httpx.HTTPError as exc:
        raise FalError("視覺服務暫時無法連線，請稍後重試。", code="vision_network") from exc

    if response.status_code in {401, 403}:
        logger.warning("fal request rejected: status=%s", response.status_code)
        raise FalError("視覺服務暫時無法使用，請改以文字描述或稍後重試。", code="vision_permission")
    if response.status_code == 429 or response.status_code >= 500:
        logger.warning("fal service unavailable: status=%s", response.status_code)
        raise FalError("視覺服務目前忙碌，請稍後重試。", code="vision_busy")
    if response.status_code >= 400:
        logger.warning("fal rejected visual request: status=%s", response.status_code)
        raise FalError("這張圖片暫時無法處理，請換一張或改以文字描述。", code="vision_invalid_input")

    try:
        data = response.json()
    except ValueError as exc:
        logger.warning("fal returned non-JSON response")
        raise FalError("視覺服務回傳格式異常，請稍後重試。", code="vision_invalid_response") from exc
    if not isinstance(data, dict):
        raise FalError("視覺服務回傳格式異常，請稍後重試。", code="vision_invalid_response")
    return data


async def describe_images(attachments: list[dict[str, str]]) -> str:
    """以 fal Vision 取得只供本輪文字代理使用的圖片摘要。"""
    urls = [item.get("data_url", "") for item in attachments if item.get("data_url")]
    if not urls:
        return ""
    payload = {
        "image_urls": urls,
        "prompt": (
            "請以繁體中文整理圖片中可見、可讀取且與使用者任務有關的資訊。"
            "請列出主題、重要文字、版面或視覺元素；不確定處請明確標示「無法確認」，"
            "不要猜測個資、身分或未呈現的事實。此摘要只會提供給文字代理完成本次任務。"
        ),
        "model": config.FAL_VISION_MODEL,
    }
    data = await _post_json("https://fal.run/openrouter/router/vision", payload)
    output = data.get("output")
    if not isinstance(output, str) or not output.strip():
        raise FalError("圖片理解沒有取得可用結果，請換一張圖片或補充文字描述。", code="vision_empty")
    return output.strip()[:6000]


async def generate_image(prompt: str) -> list[dict[str, Any]]:
    """以 fal 圖片模型產出單張視覺稿，回傳可安全嵌入的 HTTPS 圖片網址。"""
    data = await _post_json(
        f"https://fal.run/{config.FAL_IMAGE_MODEL}",
        {"prompt": prompt, "image_size": "square_hd", "num_images": 1},
    )
    raw_images = data.get("images")
    if not isinstance(raw_images, list):
        raise FalError("視覺稿沒有取得可用圖片，請調整描述後再試。", code="image_empty")
    images: list[dict[str, Any]] = []
    for item in raw_images:
        if not isinstance(item, dict):
            continue
        url = item.get("url")
        if not isinstance(url, str) or not url.startswith("https://"):
            continue
        images.append({
            "url": url,
            "content_type": str(item.get("content_type") or "image/jpeg"),
            "width": item.get("width"),
            "height": item.get("height"),
        })
    if not images:
        raise FalError("視覺稿沒有取得可用圖片，請調整描述後再試。", code="image_empty")
    return images
