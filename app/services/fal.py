"""fal.ai 視覺服務。

文字代理仍走既有 NVIDIA/OpenAI 相容路徑；這個模組只處理圖片理解與使用者
主動要求的視覺稿生成。圖片 data URL 不寫入資料庫，也不記錄在 log。
"""

from __future__ import annotations

import json
import logging
import re
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


def _structured_output(raw: str) -> dict[str, Any]:
    """接受模型偶爾包上的 markdown fence，但拒絕非物件結果。"""
    text = raw.strip()
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise FalError("圖片分析回傳格式異常，請稍後重試。", code="vision_invalid_response")
    try:
        value = json.loads(match.group(0))
    except ValueError as exc:
        raise FalError("圖片分析回傳格式異常，請稍後重試。", code="vision_invalid_response") from exc
    if not isinstance(value, dict):
        raise FalError("圖片分析回傳格式異常，請稍後重試。", code="vision_invalid_response")
    return value


async def analyze_visual_asset(data_url: str) -> dict[str, Any]:
    """視覺資料庫用的結構化 OCR／場景分析。

    提示詞明確禁止依臉推定真實姓名；人物身分只能走另外的已確認參考圖比對，
    並且比對結果也只能是 possible，仍需人工確認。
    """
    payload = {
        "image_urls": [data_url],
        "model": config.FAL_VISION_MODEL,
        "prompt": (
            "你正在替繁體中文校園社團建立可稽核的視覺資料。只回傳 JSON 物件，不要 markdown。"
            "不得根據臉孔猜測或宣稱任何人的姓名、學校、社團或身分。看不到或不確定就留空，"
            "每項辨識都要給 0 到 1 confidence 與 evidence（圖片中可指認的依據）。JSON schema："
            '{"summary":"","ocr_text":"","dates":[{"value":"YYYY-MM-DD或原文","confidence":0,"evidence":""}],'
            '"scenes":[{"label":"校園/教室/禪堂/舞台/報到區/戶外/茶會/講座/社課/聚餐/活動現場/海報/文件或其他","confidence":0,"evidence":""}],'
            '"event":{"name":"","type":"","location":"","confidence":0,"evidence":""},'
            '"clubs":[{"name":"","school":"","confidence":0,"evidence":"文字或Logo"}],'
            '"people":{"count":0,"descriptions":[{"label":"人物1","confidence":0,"evidence":"可見特徵，不含姓名"}]},'
            '"objects":[""],"logos":[{"text":"","confidence":0,"evidence":""}],'
            '"quality_notes":[""],"is_poster":false}。OCR 要保留原本繁體中文與換行。'
        ),
    }
    data = await _post_json("https://fal.run/openrouter/router/vision", payload)
    output = data.get("output")
    if not isinstance(output, str) or not output.strip():
        raise FalError("圖片理解沒有取得可用結果，請稍後重試。", code="vision_empty")
    result = _structured_output(output)
    result["ocr_text"] = str(result.get("ocr_text") or "")[:20000]
    result["summary"] = str(result.get("summary") or "")[:4000]
    return result


async def compare_confirmed_people(target_url: str, references: list[dict[str, str]]) -> list[dict[str, Any]]:
    """只在已有人工確認參考圖時提出「可能是」候選，永不直接確認。"""
    usable = [r for r in references if r.get("data_url") and r.get("person_id")][:8]
    if not usable:
        return []
    mapping = ", ".join(f"參考圖{i + 1}={r['person_id']}" for i, r in enumerate(usable))
    payload = {
        "image_urls": [target_url, *[r["data_url"] for r in usable]],
        "model": config.FAL_VISION_MODEL,
        "prompt": (
            "第1張是待分析照片，其餘是已由使用者確認身分的參考照片。"
            f"參考對照：{mapping}。只回傳 JSON："
            '{"matches":[{"person_id":"只能填上述ID","confidence":0,"evidence":"可見相似處"}]}。'
            "只有臉部清楚且確實相似才列出；不得填姓名、不得加入清單外人物、不得回傳 confirmed。"
        ),
    }
    data = await _post_json("https://fal.run/openrouter/router/vision", payload)
    output = data.get("output")
    if not isinstance(output, str):
        return []
    matches = _structured_output(output).get("matches")
    allowed = {r["person_id"] for r in usable}
    return [m for m in (matches if isinstance(matches, list) else []) if isinstance(m, dict) and m.get("person_id") in allowed]


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
