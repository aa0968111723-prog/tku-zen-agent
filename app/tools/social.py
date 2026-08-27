"""Social research/publicity tools.

Research results are reference material only.  Publicity tools write drafts to
the normal artifact directory and never publish to Instagram.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .. import retrieval
from ..research import entities as research_entities
from .base import Artifact, dated_dir, deliver, safe_filename, unique_path

EXTERNAL_WARNING = "【外校公開參考——僅供比較分析，禁止照抄，不是淡江資料】"
FOUR_HEADINGS = (
    "其他學校怎麼做",
    "為什麼可能有效",
    "淡江可以怎麼改良",
    "哪些內容不能直接照抄",
)


def _normalise_date(value: str) -> str:
    match = re.search(r"(20\d{2})[-/]?(\d{1,2})[-/]?(\d{1,2})", value or "")
    if not match:
        return ""
    return f"{match.group(1)}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"


def _date_bounds(value: str) -> tuple[str, str]:
    """從範圍字串抽出完整日期，不能把 ISO 日期本身的連字號當分隔符。"""
    matches = re.findall(r"20\d{2}[-/]?\d{1,2}[-/]?\d{1,2}", value or "")
    dates = [_normalise_date(item) for item in matches]
    dates = [item for item in dates if item]
    if not dates:
        return "", ""
    return dates[0], dates[-1]


def _references(
    query: str, top_k: int = 5, *, schools: str = "", platform: str = "",
    activity_type: str = "", date_range: str = "",
) -> list[dict[str, Any]]:
    """查外校公開參考段落。

    資料歸屬**只**來自建索引時的 entity metadata（registry 別名比對），
    不做「內容像哪間學校」的嗅探——標題或內容相似不代表屬於那間學校。
    查詢點名了特定學校時，只回傳該校（研究對象）的段落。
    """
    query = (query or "外校禪學社社群文宣").strip()
    resolution = research_entities.resolve(query)
    target_ids = {e.entity_id for e in resolution.external_targets()}
    unresolved_schools = {u.school for u in resolution.unresolved}

    index = retrieval.get_index()
    hits = index.search(
        query,
        k=max(10, min(int(top_k or 5) * 8, 50)),
        min_curated=0,
        include_external=True,
        source_types={"external_reference"},
    )
    out: list[dict[str, Any]] = []
    wanted_schools = [x.strip().lower() for x in re.split(r"[,，、\s]+", schools) if x.strip()]
    wanted_platform = (platform or "").strip().lower()
    wanted_activity = (activity_type or "").strip().lower()
    range_start, range_end = _date_bounds(date_range)

    for position, (score, chunk) in enumerate(hits, 1):
        meta = getattr(chunk, "meta", None)
        if not meta or meta.source_type != "external_reference":
            continue
        entity_id = getattr(meta, "entity_id", "")
        if (target_ids or unresolved_schools) and entity_id not in target_ids:
            continue   # 研究對象講明了，就不拿別校資料湊數
        # 學校只認 metadata 歸屬（registry 別名），不做內容嗅探
        school = getattr(meta, "school", "") or "未對應到已收錄的學校"
        blob = f"{chunk.source} {chunk.text}".lower()
        if wanted_schools and not any(w in f"{school.lower()} {blob}" for w in wanted_schools):
            continue
        if wanted_platform and wanted_platform not in blob:
            continue
        if wanted_activity and wanted_activity not in blob:
            continue
        source_date = getattr(meta, "source_date", "") or getattr(meta, "captured_at", "")
        if (range_start or range_end) and (not source_date or not (range_start <= source_date <= range_end)):
            continue
        title = getattr(meta, "source_title", "") or Path(chunk.path).stem
        url = getattr(meta, "source_url", "")
        out.append(
            {
                "source_id": f"R{position}",
                "entity_id": entity_id,
                "school": school,
                "organization": getattr(meta, "organization", ""),
                "source_file": Path(chunk.path).name,
                "title": title,
                "source": chunk.source,
                "url": url,
                "source_url": url,
                "date": source_date,
                "captured_at": getattr(meta, "captured_at", ""),
                "source_type": getattr(meta, "external_source_type", "") or "official_instagram",
                "authority_level": getattr(meta, "authority_level", "official"),
                "summary": getattr(meta, "summary", "") or chunk.text[:320],
                "excerpt": chunk.text[:900],
                "score": round(float(score), 4),
                "credibility": round(0.8 if url and source_date else (0.55 if url else 0.3), 2),
                "verification": "verified" if url and source_date else "needs_verification",
                "verified": bool(url and source_date),
                # 結構化來源的標準欄位名（研究輸出規格）：
                # source_title / source_url / publisher / captured_at /
                # source_type / supporting_excerpt / access_status
                "source_title": title,
                "publisher": getattr(meta, "organization", "") or school,
                "supporting_excerpt": chunk.text[:600],
                "access_status": (
                    "內部參考庫節錄（整理自公開頁面），本次未即時重新驗證"
                    if url and source_date
                    else "內部參考庫節錄，來源資訊不完整，未通過驗證"
                ),
            }
        )
    return out[: max(1, min(int(top_k or 5), 10))]


def _four_sections(query: str, refs: list[dict[str, Any]]) -> dict[str, str]:
    schools = ", ".join(sorted({r["school"] for r in refs})) or "目前沒有可引用的外校資料"
    citations = "、".join(f"〔來源 {r['source_id']}〕" for r in refs) or "〔無可驗證來源〕"
    return {
        FOUR_HEADINGS[0]: f"可引用的公開參考學校：{schools}。請以來源摘錄為準，不把它們當成淡江事實。{citations}",
        FOUR_HEADINGS[1]: f"可能有效的原因只能作為假設，仍需依淡江學生受眾、校園情境與實際成效驗證。{citations}",
        FOUR_HEADINGS[2]: f"淡江可保留目標與方法，改寫成淡江自己的語氣、活動與 current_term 已確認資訊。{citations}",
        FOUR_HEADINGS[3]: f"不得直接複製外校社名、講師、連結、日期、標語或長句；外校內容僅供比較分析。{citations}",
    }


def _no_reference_result(query: str) -> dict[str, Any]:
    resolution = research_entities.resolve(query)
    schools = "、".join(
        sorted({u.school for u in resolution.unresolved}
               | {e.school for e in resolution.no_source_entities if e.school})
    )
    subject = f"「{schools}」" if schools else f"「{query}」"
    return {
        "ok": True,
        "query": query,
        "references": [],
        "source_filenames": [],
        "schools": [],
        "sections": {},
        "no_verified_source": True,
        "disclaimer": "本次無法驗證公開來源，以下僅為一般策略推測，不得視為該校現況。",
        "message": (
            "本次無法驗證公開來源，以下僅為一般策略推測，不得視為該校現況。\n\n"
            f"外校公開資料庫裡**沒有**{subject}的已驗證來源。\n"
            "不可以推測或用淡江資料頂替該校事實。回覆使用者時請直接說明：\n"
            "「目前沒有足夠公開來源確認此資訊，因此不提供確定結論。」\n"
            "並請使用者提供該校社團的官方 Instagram、Facebook 或網址。"
        ),
    }


def _research_result(query: str, refs: list[dict[str, Any]]) -> dict[str, Any]:
    if not refs:
        return _no_reference_result(query)
    sections = _four_sections(query, refs)
    blocks: list[str] = []
    for r in refs:
        head = f"【外校已驗證資料】〔來源 {r['source_id']}〕{r['organization'] or r['school']}（學校：{r['school']}"
        if r.get("source_url"):
            head += f"，來源：{r['source_url']}"
        if r.get("captured_at"):
            head += f"，檢索日期：{r['captured_at']}"
        head += "）"
        blocks.append(f"{head}\n{r['excerpt']}")
    message = (
        "\n\n".join(f"## {name}\n{value}" for name, value in sections.items())
        + "\n\n───────────\n\n"
        + "\n\n".join(blocks)
        + "\n\n───────────\n描述外校做法時，只能引用上面摘錄的內容並標注學校名稱；"
          "摘錄裡沒有的細節不可以自行補寫。"
          "以上來源出自社團內部整理的公開帳號參考庫（非本次即時網路搜尋），"
          "不可以寫成「剛搜尋到」或「即時現況」。"
    )
    unverified = [r["source_id"] for r in refs if not r.get("verified")]
    if unverified:
        message = (
            "部分來源缺少可驗證網址或日期（" + "、".join(unverified) + "），以下只能作為待驗證參考。\n\n" + message
        )
    return {
        "ok": True,
        "query": query,
        "references": refs,
        "source_records": refs,
        "source_filenames": sorted({r["source_file"] for r in refs}),
        "schools": sorted({r["school"] for r in refs}),
        "sections": sections,
        "message": message,
    }


def search_social_references(
    query: str, top_k: int = 5, schools: str = "", platform: str = "",
    activity_type: str = "", date_range: str = "",
) -> dict:
    query = f"{query} {schools} {platform} {activity_type}".strip()
    return _research_result(
        query,
        _references(query, top_k, schools=schools, platform=platform, activity_type=activity_type, date_range=date_range),
    )


def compare_social_strategies(
    topic: str, schools: str = "", platform: str = "", activity_type: str = "",
    date_range: str = "", top_k: int = 5,
) -> dict:
    query = f"{topic} {schools} {platform} {activity_type}".strip()
    return _research_result(
        query,
        _references(query, top_k, schools=schools, platform=platform, activity_type=activity_type, date_range=date_range),
    )


def analyze_social_positioning(
    topic: str, audience: str = "淡江大學生", platform: str = "", activity_type: str = "",
    date_range: str = "", top_k: int = 5,
) -> dict:
    query = f"{topic} {audience} {platform} {activity_type}".strip()
    return _research_result(
        query,
        _references(query, top_k, platform=platform, activity_type=activity_type, date_range=date_range),
    )


def _write_markdown(filename: str, title: str, body: str) -> dict:
    body = (body or "").strip()
    if not body:
        body = "請補充這份社群草稿的內容。\n\n" + EXTERNAL_WARNING
    markdown = f"# {title or '淡江社群草稿'}\n\n{body}\n\n---\n\n{EXTERNAL_WARNING}"
    path = unique_path(dated_dir(), safe_filename(filename, ".md"))
    path.write_text(markdown, encoding="utf-8")
    artifact = Artifact(filename=path.name, local_path=path, summary="社群 Markdown 草稿")
    return {**deliver(path, "local", artifact, mime="text/markdown").to_result(), "markdown": True}


def create_social_post(filename: str, content: str, title: str = "淡江社群貼文") -> dict:
    return _write_markdown(filename, title, content)


def create_social_carousel(filename: str, content: str, title: str = "淡江社群輪播") -> dict:
    return _write_markdown(filename, title, content)


def create_social_story(filename: str, content: str, title: str = "淡江限時動態") -> dict:
    return _write_markdown(filename, title, content)


def create_reels_script(filename: str, content: str, title: str = "淡江 Reels 腳本") -> dict:
    return _write_markdown(filename, title, content)


def create_social_content_calendar(filename: str, content: str, title: str = "淡江社群內容日曆") -> dict:
    return _write_markdown(filename, title, content)


def create_social_ab_test(filename: str, content: str, title: str = "淡江社群 A/B 測試") -> dict:
    return _write_markdown(filename, title, content)


def create_social_image_prompt(filename: str, content: str, title: str = "淡江社群圖片提示詞") -> dict:
    return _write_markdown(filename, title, content)


def create_social_video_prompt(filename: str, content: str, title: str = "淡江社群影片提示詞") -> dict:
    return _write_markdown(filename, title, content)


def _schema(name: str, description: str, properties: dict[str, Any], required: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required},
        },
    }


_RESEARCH_FILTERS = {
    "schools": {"type": "string", "description": "指定學校，可用逗號分隔"},
    "platform": {"type": "string", "description": "指定平台，例如 Instagram、Facebook、網站"},
    "activity_type": {"type": "string", "description": "指定活動類型，例如招生、茶會、社課、講座"},
    "date_range": {"type": "string", "description": "日期範圍，例如 2026-01-01~2026-08-24"},
}
SEARCH_SCHEMA = _schema(
    "search_social_references", "搜尋外校公開社群資料，僅供比較分析；可指定學校、平台、活動類型與日期範圍。",
    {"query": {"type": "string"}, **_RESEARCH_FILTERS, "top_k": {"type": "integer"}}, ["query"]
)
COMPARE_SCHEMA = _schema(
    "compare_social_strategies", "比較外校社群策略並回傳附來源的四段式分析。",
    {"topic": {"type": "string"}, **_RESEARCH_FILTERS, "top_k": {"type": "integer"}}, ["topic"]
)
ANALYZE_SCHEMA = _schema(
    "analyze_social_positioning", "分析外校定位並提出附來源的淡江改良方向。",
    {"topic": {"type": "string"}, "audience": {"type": "string"}, **_RESEARCH_FILTERS, "top_k": {"type": "integer"}}, ["topic"]
)

_CREATE_PROPS = {"filename": {"type": "string"}, "title": {"type": "string"}, "content": {"type": "string"}}
POST_SCHEMA = _schema("create_social_post", "產出可下載的 Markdown 社群貼文草稿。", _CREATE_PROPS, ["filename", "content"])
CAROUSEL_SCHEMA = _schema("create_social_carousel", "產出可下載的 Markdown 輪播草稿。", _CREATE_PROPS, ["filename", "content"])
STORY_SCHEMA = _schema("create_social_story", "產出可下載的 Markdown 限動草稿。", _CREATE_PROPS, ["filename", "content"])
REELS_SCHEMA = _schema("create_reels_script", "產出可下載的 Markdown Reels 腳本。", _CREATE_PROPS, ["filename", "content"])
CALENDAR_SCHEMA = _schema("create_social_content_calendar", "產出可下載的 Markdown 社群內容日曆。", _CREATE_PROPS, ["filename", "content"])
AB_TEST_SCHEMA = _schema("create_social_ab_test", "產出可下載的 Markdown 社群 A/B 測試草稿。", _CREATE_PROPS, ["filename", "content"])
IMAGE_SCHEMA = _schema("create_social_image_prompt", "產出可下載的 Markdown 圖片提示詞。", _CREATE_PROPS, ["filename", "content"])
VIDEO_SCHEMA = _schema("create_social_video_prompt", "產出可下載的 Markdown 影片提示詞。", _CREATE_PROPS, ["filename", "content"])
