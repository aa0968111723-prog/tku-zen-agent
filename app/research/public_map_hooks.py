"""把公開研究地圖接到既有 retrieval / metadata / tools 的薄接點。

這些函式都是 additive：
- 找不到既有模組時仍可單獨運作
- 不改內部 SSOT
- 不擴大到社群爬蟲
"""

from __future__ import annotations

from typing import Any

from .public_map import (
    PUBLIC_SOURCE_ENTITIES,
    allowlist_hosts,
    entities_matching,
    is_fetchable_url,
    source_record,
)


PUBLIC_SOURCE_DIR_HINTS = ("公開來源", "public_local", "public_research")
PUBLIC_LABEL = "公開來源，不是社團規定"


def infer_entity_id(path_or_query: str) -> str | None:
    text = path_or_query or ""
    if "八類公開來源研究地圖" in text:
        return None  # 整張地圖，不強行綁單一 entity
    hits = entities_matching(text)
    if len(hits) == 1:
        return hits[0].id
    return None


def decorate_chunk_meta(meta: dict[str, Any], *, path: str = "", query: str = "") -> dict[str, Any]:
    """為公開來源 chunk 補 provenance，並禁止標成社團 SSOT。"""
    out = dict(meta)
    path_l = path.replace("\\", "/")
    is_public = (
        "公開來源" in path_l
        or out.get("source_type") in {e.source_kind for e in PUBLIC_SOURCE_ENTITIES} | {"public_local"}
        or str(out.get("source", "")).startswith("公開來源")
    )
    if not is_public:
        return out

    entity_id = out.get("entity_id") or infer_entity_id(path) or infer_entity_id(query)
    if entity_id:
        record = source_record(entity_id, source_url=out.get("source_url"), captured_at=out.get("captured_at"))
        out.update({k: v for k, v in record.items() if v is not None})
    else:
        out.setdefault("source_type", "public_local")
        out.setdefault("authority_level", "official")
        out["is_club_ssot"] = False
        out["attribution"] = PUBLIC_LABEL

    out["is_club_ssot"] = False
    if out.get("organization") == "淡江大學領袖禪學社":
        out["organization"] = out.get("name") or "公開來源"
    out["attribution"] = PUBLIC_LABEL
    return out


def chunk_is_public_source(chunk: Any) -> bool:
    meta = getattr(chunk, "meta", None)
    source = str(getattr(chunk, "source", "") or "")
    source_type = getattr(meta, "source_type", "") if meta is not None else ""
    if hasattr(meta, "get"):
        source_type = meta.get("source_type", source_type)
    return source_type in {e.source_kind for e in PUBLIC_SOURCE_ENTITIES} | {"public_local"} or source.startswith("公開來源")


def label_for_chunk(chunk: Any) -> str:
    if chunk_is_public_source(chunk):
        return PUBLIC_LABEL
    return "淡江內部資料"


def extra_allowlist_hosts() -> frozenset[str]:
    """給 public_sources 服務擴充用。不含 facebook / instagram。"""
    return allowlist_hosts()


def guard_fetch_url(url: str) -> dict:
    if is_fetchable_url(url):
        return {"ok": True, "url": url}
    return {
        "ok": False,
        "code": "fetch_blocked",
        "message": (
            "這個網址不在公開研究地圖的 HTTPS 官方 allowlist，"
            "或屬於社群／HTTP／登入牆。請改給入口網址，或用 search_perplexity_web 查公開頁並保留出處。"
        ),
    }


def list_map_brief() -> list[dict]:
    rows = []
    for entity in PUBLIC_SOURCE_ENTITIES:
        rows.append(
            {
                "id": entity.id,
                "category": entity.category,
                "name": entity.name,
                "official_https": list(entity.official_https),
                "reference_only": list(entity.reference_only),
                "fetch_policy": entity.fetch_policy,
                "attribution": PUBLIC_LABEL,
            }
        )
    return rows
