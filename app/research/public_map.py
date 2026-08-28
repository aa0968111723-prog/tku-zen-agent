"""資料驅動的公開研究入口地圖。

這個模組只保存已核對的 HTTPS 入口與 provenance 規則；它不是爬蟲、
社團 SSOT，也不會從社群網站推論人物或組織身分。公開頁面要不要即時
讀取仍由 ``public_sources`` 的 server-side allowlist 決定。

PR #33 合併後 main 先放精簡 stub，本檔在不改 entity id 的前提下
補上 2026-08-27 核過的 HTTPS 官方入口。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlparse


SOCIAL_HOSTS = frozenset(
    {
        "facebook.com",
        "www.facebook.com",
        "m.facebook.com",
        "l.facebook.com",
        "instagram.com",
        "www.instagram.com",
        "x.com",
        "twitter.com",
        "www.twitter.com",
        "linkedin.com",
        "www.linkedin.com",
    }
)


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


PUBLIC_SOURCE_ENTITIES: tuple[PublicSourceEntity, ...] = (
    PublicSourceEntity(
        id="tku-university",
        category="淡江大學",
        name="淡江大學",
        source_kind="public_university",
        official_https=(
            "https://www.tku.edu.tw/",
            "https://about.tku.edu.tw/campus.html",
            "https://www.lib.tku.edu.tw/",
            "https://sa.tku.edu.tw/spirit/",
            "https://acad.tku.edu.tw/",
            "https://english.tku.edu.tw/",
            "https://etds.lib.tku.edu.tw/",
            "https://www.history.tku.edu.tw/",
            "https://archives.lib.tku.edu.tw/",
        ),
        reference_only=("http://tkuir.lib.tku.edu.tw:8080/",),
        fetch_policy="只讀淡江官方 HTTPS 頁面，保留來源與擷取時間。HTTP 機構典藏只給網址。",
        aliases=("淡江", "淡江大學", "淡大", "tku", "Tamkang University", "TKU"),
    ),
    PublicSourceEntity(
        id="tku-food-map",
        category="淡大美食地圖",
        name="淡大美食地圖",
        source_kind="public_university",
        official_https=(
            "https://www.tku.edu.tw/tku/?page_id=8453",
            "https://mail.tku.edu.tw/yjlin/map.htm",
            "https://english.tku.edu.tw/foodmap-detail.asp?id=4",
            "https://www.tku.edu.tw/",
        ),
        reference_only=(),
        fetch_policy="官方美食頁與美食廣場地圖可當入口；不是即時店家庫，菜單價格不可編造。",
        aliases=("淡大美食", "美食地圖", "淡江美食", "校園美食", "松濤館美食廣場", "Tamkang Top Eats"),
    ),
    PublicSourceEntity(
        id="tamsui-local",
        category="淡水地區",
        name="淡水地區",
        source_kind="public_local",
        official_https=(
            "https://tamsui.dils.tku.edu.tw/",
            "https://www.lib.tku.edu.tw/",
            "https://www.tamsui.ntpc.gov.tw/",
            "https://data.ntpc.gov.tw/",
            "https://www.tamsui.land.ntpc.gov.tw/",
            "https://www.ntchgis.ntpc.gov.tw/",
        ),
        reference_only=(
            "http://tamsui.dils.tku.edu.tw/",
            "http://tkuir.lib.tku.edu.tw:8080/",
        ),
        fetch_policy="HTTPS 官方入口可讀；HTTP 淡水維基館舊網址僅作參考，不進即時抓取白名單。",
        aliases=("淡水", "淡水地區", "淡水資料", "淡水維基館", "Tamsui", "Danshui", "淡水區"),
    ),
    PublicSourceEntity(
        id="wlpef",
        category="世界領袖教育和平基金會",
        name="世界領袖教育和平基金會",
        source_kind="public_foundation",
        official_https=(
            "https://www.wlpef.org/",
            "https://www.wlpef.org/?page_id=4957",
            "https://www.wlef.org/",
            "https://www.wlef.org/president/",
            "https://www.wlef.org/teacherandclass/",
            "https://www.wlef.org/wlefglobal/",
        ),
        reference_only=(
            "https://www.instagram.com/wlpef.official/",
            "https://www.facebook.com/wlpefofficial",
        ),
        fetch_policy="只作公開組織入口導覽，不把公開頁面內容當作淡大社團今年的規定。社群不抓。",
        aliases=("世界領袖教育和平基金會", "世界領袖教育基金會", "領袖會", "wlpef", "wlef", "WLPEF"),
    ),
    PublicSourceEntity(
        id="related-university-clubs",
        category="旗下或相關大學社團",
        name="旗下或相關大學社團",
        source_kind="public_club_directory",
        official_https=(
            "https://sa.tku.edu.tw/spirit/",
            "https://sites.google.com/view/tkuclubsfresh/%E6%88%91%E8%A6%81%E6%89%BE%E7%A4%BE%E5%9C%98/%E5%AD%B8%E8%97%9D%E6%80%A7%E7%A4%BE%E5%9C%98/%E9%A0%98%E8%A2%96%E7%A6%AA%E5%AD%B8%E7%A4%BE",
            "https://osa_activity.ntu.edu.tw/club/detail/sn/0867",
            "https://activity.usc.edu.tw/p/405-1062-31762,c603.php?Lang=zh-tw",
            "https://itouch.cycu.edu.tw/active_system/active_group/hrcb001_Detail.jsp?ASSN_NO=C008",
            "https://osa.nccu.edu.tw/zh-CN/tw/%E8%AA%B2%E5%A4%96%E6%B4%BB%E5%8B%95%E7%B5%84/%E7%A4%BE%E5%9C%98-%E8%AA%B2%E5%A4%96%E7%B5%84%E7%BE%A9%E5%B7%A5%E5%9C%98%E8%B3%87%E8%A8%8A/%E7%A4%BE%E5%9C%98%E7%B6%B2%E9%A0%81%E9%80%A3%E7%B5%90/%E5%AD%B8%E8%A1%93%E6%80%A7%E7%A4%BE%E5%9C%98",
        ),
        reference_only=(
            "https://www.facebook.com/tkuLeaderZen",
            "https://www.facebook.com/ntuleadership/",
            "http://club.mcu.edu.tw/ylc/zh-hant/node/1",
            "http://spirit.tku.edu.tw/tku/file/section3/service/19/296/",
        ),
        fetch_policy="只提供已核對的各校學務處／官方社團 HTTPS 示例，不是全國總冊。",
        aliases=("大學社團", "相關社團", "兄弟社團", "領袖社", "禪學社", "旗下社團", "各校領袖社"),
    ),
    PublicSourceEntity(
        id="youth-groups",
        category="社青團",
        name="社青團",
        source_kind="public_alumni_network",
        official_https=("https://www.wlpef.org/",),
        reference_only=("https://www.facebook.com/wlpefyouth",),
        fetch_policy="沒有獨立官網；只列領袖會官網公開敘述。Facebook 專頁只註記、不抓取。",
        aliases=("社青團", "青年團體", "社會青年", "領袖會社青團", "WLPEF Youth"),
    ),
    PublicSourceEntity(
        id="master-teachings",
        category="悟覺妙天禪師開示",
        name="悟覺妙天禪師開示",
        source_kind="public_teachings_portal",
        official_https=(
            "https://www.zencosmos.com.tw/",
            "https://www.zencosmos.com.tw/category/master/",
            "https://www.wlef.org/president/",
            "https://www.buddhachan.org/",
            "https://www.buddhachan.org/master/biography/",
        ),
        reference_only=(),
        fetch_policy="只登記公開入口與目錄。禁止把開示全文灌進社團知識庫。",
        aliases=("悟覺妙天", "悟覺妙天禪師", "開示", "印心禪法", "禪師開示", "宗師開示"),
    ),
    PublicSourceEntity(
        id="zen-world",
        category="禪天下",
        name="禪天下",
        source_kind="public_media",
        official_https=(
            "https://www.zencosmos.com.tw/",
            "https://www.zencosmos.com.tw/catalog",
            "https://www.zencosmos.com.tw/category/hot-topic/special-feature/",
            "https://www.buddhachan.org/",
        ),
        reference_only=(),
        fetch_policy="僅作公開出版／媒體入口；文章受著作權保護，不整期轉存。",
        aliases=("禪天下", "禪天下雜誌", "ZenCosmos", "Zen Cosmos", "zencosmos"),
    ),
)


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
    """回傳公開研究地圖允許的 HTTPS host。不含社群網域。"""

    hosts: set[str] = set()
    for entity in PUBLIC_SOURCE_ENTITIES:
        for url in entity.official_https:
            host = (urlparse(url).hostname or "").lower().rstrip(".")
            if host and host not in SOCIAL_HOSTS and not host.endswith(".facebook.com"):
                hosts.add(host)
    return frozenset(hosts)


def is_fetchable_url(url: str) -> bool:
    """只允許地圖中的 HTTPS 入口或其官方子路徑。社群與 HTTP 一律否。"""

    candidate = (url or "").strip()
    try:
        parsed = urlparse(candidate)
    except ValueError:
        return False
    if parsed.scheme != "https" or not parsed.hostname:
        return False
    host = parsed.hostname.lower().rstrip(".")
    if host in SOCIAL_HOSTS or host.endswith(".facebook.com"):
        return False
    if candidate in {item for entity in PUBLIC_SOURCE_ENTITIES for item in entity.reference_only}:
        return False
    allowed = allowlist_hosts()
    return host in allowed or any(host.endswith("." + root) for root in allowed)


def _entity(entity_id: str) -> PublicSourceEntity | None:
    return next((item for item in PUBLIC_SOURCE_ENTITIES if item.id == entity_id), None)


def entity_by_id(entity_id: str) -> PublicSourceEntity | None:
    return _entity(entity_id)


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
        "category": entity.category,
        "name": entity.name,
        "source_title": entity.name,
        "source_url": url,
        "publisher": entity.name,
        "organization": entity.name,
        "captured_at": timestamp,
        "source_type": entity.source_kind,
        "access_status": "公開來源入口；非本次即時網路搜尋",
        "authority_level": "official",
        "is_club_ssot": False,
        "has_sources": True,
        "fetch_policy": entity.fetch_policy,
        "attribution": "公開來源，不是社團規定",
    }


REQUIRED_CATEGORIES: tuple[str, ...] = tuple(entity.id for entity in PUBLIC_SOURCE_ENTITIES)
REQUIRED_CATEGORY_NAMES: tuple[str, ...] = (
    "淡江大學",
    "淡大美食地圖",
    "淡水地區",
    "世界領袖教育和平基金會",
    "旗下或相關大學社團",
    "社青團",
    "悟覺妙天禪師開示",
    "禪天下",
)
