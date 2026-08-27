"""Static, verified entry-point map for public research.

This is a leaf module: it contains immutable data and URL validation only.
It never imports ``app.research`` (or any RAG/provider/database module), and
it never fetches a URL while being imported.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True)
class PublicSourceEntity:
    id: str
    category: str
    name: str
    aliases: tuple[str, ...]
    official_https: tuple[str, ...]
    reference_only: tuple[str, ...] = ()
    fetch_policy: str = "official_https_only"
    source_kind: str = "public_local"


REQUIRED_CATEGORIES = (
    "淡江大學", "淡大美食地圖", "淡水地區", "世界領袖教育和平基金會",
    "大學社團", "社青團", "悟覺妙天禪師開示", "禪天下",
)


PUBLIC_SOURCE_ENTITIES = (
    PublicSourceEntity("tku", "淡江大學", "淡江大學官方入口", ("淡江", "淡大", "淡江大學"), ("https://www.tku.edu.tw/",), source_kind="tku_official"),
    PublicSourceEntity("tku-food-map", "淡大美食地圖", "淡江校園與周邊生活資訊入口", ("淡大美食", "淡江美食", "美食地圖", "淡大吃什麼"), ("https://www.tku.edu.tw/",), source_kind="tku_food_public"),
    PublicSourceEntity("tamsui", "淡水地區", "淡水區公所官方入口", ("淡水", "淡水地區", "淡水生活"), ("https://www.tamsui.ntpc.gov.tw/",), source_kind="tamsui_public"),
    PublicSourceEntity("wlpef", "世界領袖教育和平基金會", "世界領袖教育和平基金會官方入口", ("世界領袖教育和平基金會", "領袖教育和平基金會", "和平基金會", "WLPEF"), ("https://www.wlef.org/",), source_kind="wlpef_official"),
    PublicSourceEntity("university-clubs", "大學社團", "旗下或相關大學社團公開入口", ("大學社團", "大學禪學社", "領袖社", "各校社團", "旗下社團"), ("https://www.wlef.org/",), source_kind="university_club_public"),
    PublicSourceEntity("youth-groups", "社青團", "社青團公開入口", ("社青團", "青年團", "社會青年"), ("https://www.wlef.org/",), source_kind="youth_group_public"),
    PublicSourceEntity("wujue-miaotian", "悟覺妙天禪師開示", "悟覺妙天禪師公開開示入口", ("悟覺妙天禪師", "悟覺妙天", "禪師開示", "印心禪法"), ("https://www.buddhachan.org/",), source_kind="wujue_miaotian_public"),
    PublicSourceEntity("chantian", "禪天下", "禪天下公開入口", ("禪天下", "禪天下雜誌"), ("https://www.buddhachan.org/",), source_kind="chantian_public"),
)

_BY_ID = {entity.id: entity for entity in PUBLIC_SOURCE_ENTITIES}
_HOSTS = frozenset(
    parsed.hostname
    for entity in PUBLIC_SOURCE_ENTITIES
    for parsed in (urlsplit(url) for url in entity.official_https)
    if parsed.hostname
)


def allowlist_hosts() -> frozenset[str]:
    return _HOSTS


def is_fetchable_url(url: str | None) -> bool:
    """Allow only HTTPS URLs on the map's exact official hosts."""
    if not isinstance(url, str) or not url.strip():
        return False
    try:
        parsed = urlsplit(url.strip())
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme.lower() == "https"
        and not parsed.username
        and not parsed.password
        and port is None
        and parsed.hostname is not None
        and parsed.hostname.lower() in _HOSTS
    )


def entities_matching(text: str | None) -> tuple[PublicSourceEntity, ...]:
    value = (text or "").casefold()
    return tuple(
        entity
        for entity in PUBLIC_SOURCE_ENTITIES
        if any(term.casefold() in value for term in (entity.id, entity.category, entity.name, *entity.aliases))
    )


def source_record(entity_id: str, *, source_url: str | None = None, captured_at: str | None = None) -> dict[str, object]:
    """Return provenance metadata for a map entry without doing I/O."""
    entity = _BY_ID.get(entity_id)
    if entity is None:
        return {}
    url = source_url if source_url in entity.official_https else entity.official_https[0]
    return {
        "entity_id": entity.id,
        "source_url": url,
        "source_type": entity.source_kind,
        "external_source_type": "official_website",
        "authority_level": "official",
        "source_scope": "external",
        "organization": entity.name,
        "captured_at": captured_at or "",
        "is_club_ssot": False,
        "attribution": "公開來源，不是社團規定",
    }
