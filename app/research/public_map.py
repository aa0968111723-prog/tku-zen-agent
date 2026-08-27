"""資料驅動的公開研究入口地圖。

這個模組只保存已核對的 HTTPS 入口與 provenance 規則；它不是爬蟲、
社團 SSOT，也不會從社群網站推論人物或組織身分。公開頁面要不要即時
讀取仍由 ``public_sources`` 的 server-side allowlist 決定。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlparse


@dataclass(frozen=True)
class PublicSourceEntity:
    """一個可供研究導覽使用的公開來源類別。"""

    id: str
    category: str
    name: str
    source_kind: str
    official_https: tuple[str, ...]
    reference_only: tuple[str, ...]
    fetch_policy: str
    aliases: tuple[str, ...] = ()


# 入口故意保持小而明確。reference_only 可以幫助搜尋理解脈絡，
# 但不會被 is_fetchable_url 視為可直接抓取的來源。
PUBLIC_SOURCE_ENTITIES: tuple[PublicSourceEntity, ...] = (
    PublicSourceEntity(
        id="tku-university",
        category="大學官方",
        name="淡江大學",
        source_kind="tku_university",
        official_https=("https://www.tku.edu.tw/",),
        reference_only=("https://www.tku.edu.tw/",),
        fetch_policy="只讀淡江官方 HTTPS 頁面，保留來源與擷取時間。",
        aliases=("淡江", "淡江大學", "tku"),
    ),
    PublicSourceEntity(
        id="tku-food-map",
        category="校園生活",
        name="淡大美食地圖",
        source_kind="tku_food_map",
        official_https=("https://www.tku.edu.tw/",),
        reference_only=(),
        fetch_policy="目前只提供入口導覽；沒有可核對的專用 HTTPS 入口時不假裝即時查詢。",
        aliases=("淡大美食", "美食地圖", "淡江美食"),
    ),
    PublicSourceEntity(
        id="tamsui-local",
        category="地方資料",
        name="淡水地區",
        source_kind="tamsui_local",
        official_https=(
            "https://www.tamsui.ntpc.gov.tw/",
            "https://data.ntpc.gov.tw/",
        ),
        reference_only=("http://tamsui.dils.tku.edu.tw/",),
        fetch_policy="只抓 HTTPS 官方入口；HTTP 淡水維基館僅作參考，不進即時抓取白名單。",
        aliases=("淡水", "淡水地區", "淡水資料", "淡水維基館"),
    ),
    PublicSourceEntity(
        id="wlpef",
        category="基金會／組織",
        name="世界領袖教育和平基金會",
        source_kind="wlpef",
        official_https=("https://www.wlef.org/",),
        reference_only=(),
        fetch_policy="只作公開組織入口導覽，不把公開頁面內容當作淡大社團今年的規定。",
        aliases=("世界領袖教育和平基金會", "世界領袖教育基金會", "wlpef", "wlef"),
    ),
    PublicSourceEntity(
        id="related-university-clubs",
        category="相關大學社團",
        name="旗下或相關大學社團",
        source_kind="related_university_club",
        official_https=("https://club.sis.tku.edu.tw/",),
        reference_only=(),
        fetch_policy="只提供已知官方入口；不可由公開貼文推定社團隸屬或現況。",
        aliases=("大學社團", "相關社團", "兄弟社團", "領袖社", "禪學社"),
    ),
    PublicSourceEntity(
        id="youth-groups",
        category="社青團",
        name="社青團",
        source_kind="youth_group",
        official_https=("https://www.wlef.org/",),
        reference_only=(),
        fetch_policy="只列公開入口；沒有可驗證頁面時回報查不到，不捏造即時活動。",
        aliases=("社青團", "青年團體", "社會青年"),
    ),
    PublicSourceEntity(
        id="master-teachings",
        category="開示與法脈資料",
        name="悟覺妙天禪師開示",
        source_kind="master_teachings",
        official_https=("https://www.buddhachan.org/",),
        reference_only=(),
        fetch_policy="公開教學內容需保留原始來源，不宣稱人物身分由影像辨識確認。",
        aliases=("悟覺妙天", "悟覺妙天禪師", "開示", "印心禪法"),
    ),
    PublicSourceEntity(
        id="zen-world",
        category="出版與媒體",
        name="禪天下",
        source_kind="zen_world",
        official_https=("https://www.buddhachan.org/",),
        reference_only=(),
        fetch_policy="僅作公開出版／媒體入口；文章內容仍需引用原始網址與日期。",
        aliases=("禪天下", "禪天下雜誌"),
    ),
)


def _entity_text(entity: PublicSourceEntity) -> str:
    return " ".join((entity.id, entity.category, entity.name, *entity.aliases)).casefold()


def entities_matching(text: str) -> list[PublicSourceEntity]:
    """以明確別名匹配來源類別；空字串表示整張地圖。"""

    needle = (text or "").strip().casefold()
    if not needle:
        return list(PUBLIC_SOURCE_ENTITIES)
    matches: list[PublicSourceEntity] = []
    for entity in PUBLIC_SOURCE_ENTITIES:
        terms = (entity.id, entity.category, entity.name, *entity.aliases)
        if any(term.casefold() in needle or needle in term.casefold() for term in terms if term):
            matches.append(entity)
    return matches


def allowlist_hosts() -> frozenset[str]:
    """回傳公開研究地圖允許的 HTTPS host。"""

    hosts: set[str] = set()
    for entity in PUBLIC_SOURCE_ENTITIES:
        for url in entity.official_https:
            host = (urlparse(url).hostname or "").lower().rstrip(".")
            if host:
                hosts.add(host)
    return frozenset(hosts)


def is_fetchable_url(url: str) -> bool:
    """只允許地圖中的 HTTPS 入口或其官方子路徑。"""

    try:
        parsed = urlparse((url or "").strip())
    except ValueError:
        return False
    if parsed.scheme != "https" or not parsed.hostname:
        return False
    host = parsed.hostname.lower().rstrip(".")
    allowed = allowlist_hosts()
    return host in allowed or any(host.endswith("." + root) for root in allowed)


def _entity(entity_id: str) -> PublicSourceEntity | None:
    return next((item for item in PUBLIC_SOURCE_ENTITIES if item.id == entity_id), None)


def source_record(
    entity_id: str,
    *,
    source_url: object = None,
    captured_at: object = None,
) -> dict[str, object]:
    """建立可直接附加到 chunk 的來源 provenance。"""

    entity = _entity(entity_id)
    if entity is None:
        return {}
    url = str(source_url or entity.official_https[0])
    if not is_fetchable_url(url):
        url = entity.official_https[0]
    timestamp = str(captured_at or datetime.now(timezone.utc).isoformat())
    return {
        "entity_id": entity.id,
        "source_title": entity.name,
        "source_url": url,
        "publisher": entity.name,
        "captured_at": timestamp,
        "source_type": entity.source_kind,
        "access_status": "公開來源入口；非本次即時網路搜尋",
        "authority_level": "official",
        "is_club_ssot": False,
        "attribution": "公開來源，不是社團規定",
    }


REQUIRED_CATEGORIES: tuple[str, ...] = tuple(entity.id for entity in PUBLIC_SOURCE_ENTITIES)
