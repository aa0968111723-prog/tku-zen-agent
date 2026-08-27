"""Context Builder。

每一段都要明確標出**來源類型、年份與資料歸屬**。這是「歷史資料不冒充今年
事實、淡江資料不冒充外校事實」的第一道防線——模型看到「歷年範例 · 113學年度」
就知道那不是今年的數字，看到【外校已驗證資料】才可以拿來描述外校。
（第二道是 research.verifier 在回答送出前實際驗證。）

外部研究的硬規則（政大事故後訂死）：
  · 外校證據池只收 source_scope == "external" 且 entity 是研究對象的段落。
  · 淡江內部段落**永遠**進不了外校證據池——不論分數多高、內容多像。
  · 研究對象在證據池裡一段都沒有 → 誠實回報空池，不拿別人的資料頂替。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..research.types import SourceRecord
from ..research.entities import ResearchMode, ResearchScope
from .conflicts import detect_conflicts
from .hybrid import Scored, hybrid_search
from .query import parse

MAX_SNIPPET = 1100
MAX_TOTAL_CHARS = 9000

SOURCE_LABEL = {
    "curated": "社團知識庫",
    "playbook": "任務劇本",
    "archive": "歷年範例",
    "conversation": "歷史對話",
    "external_reference": "外校公開參考",
}


def _is_external_chunk(chunk) -> bool:
    return getattr(getattr(chunk, "meta", None), "source_scope", "internal") == "external"


@dataclass
class ContextBundle:
    hits: list[Scored] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)
    scope: ResearchScope | None = None
    curated_count: int = 0
    archive_count: int = 0
    external_count: int = 0
    truncated: bool = False
    conflicts: list[dict] = field(default_factory=list)
    _source_records: list[SourceRecord] | None = None

    # ── 池 ───────────────────────────────────────────────

    def external_hits(self) -> list[Scored]:
        return [s for s in self.hits if _is_external_chunk(s.chunk)]

    def internal_hits(self) -> list[Scored]:
        return [s for s in self.hits if not _is_external_chunk(s.chunk)]

    def external_evidence_for_targets(self) -> list[Scored]:
        """真正能當「研究對象證據」的外校段落（entity 對得上研究對象）。"""
        if self.scope is None:
            return self.external_hits()
        return [
            s for s in self.external_hits()
            if self.scope.accepts_external_chunk(getattr(s.chunk.meta, "entity_id", ""))
        ]

    # ── 來源卡 ───────────────────────────────────────────

    def source_records(self, current_year: str | None = None) -> list[SourceRecord]:
        if self._source_records is None:
            # Evidence conversion belongs to the feature path, not retrieval
            # startup. This keeps package import free of research integration.
            from ..research.claims import source_from_chunk

            # 有明確研究對象時，把對象傳給來源分級：不屬於研究對象的外校
            # 來源會標 wrong_entity，而不是掛著 verified 混進來源卡。
            targets: list[str] | None = None
            if self.scope is not None and self.scope.target_entities and not self.scope.generic_external:
                targets = list(self.scope.target_entities)
            self._source_records = [
                source_from_chunk(s.chunk, current_year=current_year, target_entities=targets)
                for s in self.hits
            ]
        return self._source_records

    def source_cards(self, current_year: str | None = None) -> list[dict]:
        return [r.to_card() for r in self.source_records(current_year)]

    # ── 相容 API ─────────────────────────────────────────

    def source_labels(self) -> list[str]:
        return [s.chunk.source for s in self.hits]

    def display_sources(self) -> list[str]:
        """給 UI 的簡短來源清單。"""
        out: list[str] = []
        for s in self.hits:
            meta = getattr(s.chunk, "meta", None)
            kind = SOURCE_LABEL.get(meta.source_type if meta else "archive", "資料")
            extra = meta.label() if meta else ""
            if meta and meta.source_scope == "external":
                extra = meta.organization or meta.school or extra
            label = f"{kind}：{extra}" if extra else kind
            name = s.chunk.source.split("／")[-1].split(" › ")[0][:40]
            entry = f"{label}｜{name}" if name else label
            if entry not in out:
                out.append(entry)
        return out[:8]

    # ── 提示詞區塊 ────────────────────────────────────────

    def render(self) -> str:
        """組成放進 system prompt 的段落。外校與淡江資料完全分開呈現。"""
        mode = self.scope.mode if self.scope else ResearchMode.INTERNAL
        if mode == ResearchMode.INTERNAL:
            return self._render_internal()
        return self._render_research(mode)

    def _render_internal(self) -> str:
        if not self.hits:
            return (
                "## 檢索結果\n\n知識庫裡沒有查到相關內容。\n"
                "不要編造社團專屬資訊。如果是通用做法可以用你的常識，但要說清楚"
                "那不是社團既有的規定。"
            )

        lines = [
            "## 檢索結果",
            "",
            "以下是從社團知識庫查到的內容。**每一段都標了來源類型與年份，請照著判斷可信度**：",
            "",
            "- `社團知識庫` / `任務劇本` = 規範，照著做",
            "- `歷年範例` = 往年真實檔案，格式與寫法可以照抄，"
            "**但裡面的日期、人名、金額、人數是當年的，不可以當成今年的事實**",
            "",
        ]
        if self.conflicts:
            lines += [
                "## 來源衝突提示",
                "",
                "以下資料不能直接合併成單一事實；請依 resolution 處理：",
            ]
            for conflict in self.conflicts:
                field_name = conflict.get("field", "資料")
                kind = conflict.get("kind", "conflict")
                if kind == "stale_reference":
                    lines.append(
                        f"- {field_name}：本學期值為「{conflict.get('current_value', '')}」，"
                        "檢索到不同的歷年值；以本學期資料為準。"
                    )
                else:
                    values = "、".join(str(v.get("value", "")) for v in conflict.get("values", []))
                    lines.append(f"- {field_name}（{conflict.get('year', '未確認')}）：出現不同值「{values}」。")
            lines.append("")

        self._append_blocks(lines, self.hits)
        return "\n".join(lines)

    def _render_research(self, mode: ResearchMode) -> str:
        evidence = self.external_evidence_for_targets()
        lines = ["## 檢索結果（研究模式：" + ("外部研究" if mode == ResearchMode.EXTERNAL else "比較分析") + "）", ""]

        lines.append("### 【外校已驗證資料】")
        lines.append("")
        if evidence:
            lines.append(
                "以下段落來自外校**官方公開來源**的整理，附學校、帳號與檢索日期。"
                "描述外校做法時**只能**引用這裡的內容，並附上學校名稱；"
                "摘錄裡沒有的細節不可以自行補寫。"
            )
            lines.append("")
            self._append_blocks(lines, evidence, external_header=True)
        else:
            lines.append(
                "**這次研究對象沒有任何可驗證的外校來源。**"
                "你不知道該校社團的任何事實，一個字都不可以編。"
                "回覆只能說明目前沒有足夠公開來源，並請使用者提供官方帳號或網址。"
            )
            lines.append("")

        if mode == ResearchMode.COMPARATIVE:
            internal = self.internal_hits()
            lines.append("### 【淡江內部資料】")
            lines.append("")
            if internal:
                lines.append(
                    "以下是淡江自己的資料，只能寫成「淡江的做法／淡江可採用的建議」，"
                    "**絕對不可以寫成外校已經在做的事**。歷年檔案是往年資料，不是今年事實。"
                )
                lines.append("")
                self._append_blocks(lines, internal)
            else:
                lines.append("（這次沒有檢索到相關的淡江內部資料。）")
                lines.append("")

        return "\n".join(lines)

    def _append_blocks(self, lines: list[str], hits: list[Scored], external_header: bool = False) -> None:
        used = sum(len(l) for l in lines)
        for s in hits:
            chunk = s.chunk
            meta = getattr(chunk, "meta", None)
            kind = SOURCE_LABEL.get(meta.source_type if meta else "archive", "資料")
            detail = meta.label() if meta else ""
            if external_header and meta is not None:
                if meta.organization or meta.school:
                    header = f"#### 【外校已驗證資料】{meta.organization or meta.school}"
                else:
                    # 多校彙整段落：是外部參考沒錯，但不屬於任何單一學校，
                    # 不能掛「已驗證資料」的頭銜當成研究對象的證據。
                    header = "#### 【外校公開參考——多校彙整，不可作為單一學校的證據】"
                bits = []
                if meta.school:
                    bits.append(f"學校：{meta.school}")
                if meta.source_url:
                    bits.append(f"來源：{meta.source_url}")
                if meta.captured_at:
                    bits.append(f"檢索日期：{meta.captured_at}")
                if bits:
                    header += "（" + "，".join(bits) + "）"
            else:
                header = f"#### 【{kind}】{chunk.source}"
                if meta and meta.source_type == "external_reference":
                    header = "#### 【外校公開參考——僅供比較分析，禁止照抄，不是淡江資料】 " + chunk.source
                if detail:
                    header += f"（{detail}）"

            text = chunk.text.strip()
            if len(text) > MAX_SNIPPET:
                text = text[:MAX_SNIPPET] + "…（略）"

            block = f"{header}\n\n{text}"
            if used + len(block) > MAX_TOTAL_CHARS:
                self.truncated = True
                lines.append("（其餘結果因長度限制略過）")
                break
            lines.append(block)
            lines.append("")
            used += len(block)


def _dedupe_extend(pooled: list[Scored], seen: set[int], hits: list[Scored]) -> None:
    for h in hits:
        key = id(h.chunk)
        if key not in seen:
            seen.add(key)
            pooled.append(h)


def build_context(
    queries: list[str],
    *,
    task_type: str = "",
    scope: ResearchScope | None = None,
    per_query: int = 5,
    total: int = 9,
) -> ContextBundle:
    """跑多個查詢並合併結果。

    scope 決定資料範圍：
      · internal    —— 只查淡江內部（知識庫／劇本／歷年）。
      · external    —— 只查「研究對象的外校段落」，內部資料完全不進 context。
      · comparative —— 外校證據池與淡江內部池**分開檢索、分開呈現**。

    沒帶 scope 時維持舊行為（内部＋social 任務可見外校參考），既有呼叫端不受影響。
    """
    from .. import retrieval
    from ..services import current_term as term_service

    index = retrieval.get_index()
    current_year = term_service.load().get("academic_year")

    bundle = ContextBundle(queries=list(queries), scope=scope)
    seen: set[int] = set()
    pooled: list[Scored] = []

    mode = scope.mode if scope else None

    if mode in {ResearchMode.EXTERNAL, ResearchMode.COMPARATIVE}:
        # ── 外校證據池：硬過濾，淡江段落進不來 ─────────────
        def external_filter(chunk) -> bool:
            meta = getattr(chunk, "meta", None)
            if getattr(meta, "source_scope", "internal") != "external":
                return False
            return scope.accepts_external_chunk(getattr(meta, "entity_id", ""))

        ext_pool: list[Scored] = []
        ext_seen: set[int] = set()
        for raw in queries or []:
            if not raw.strip():
                continue
            hits = hybrid_search(
                index,
                raw,
                k=per_query,
                parsed=parse(raw),
                current_year=current_year,
                min_curated=0,
                min_archive=0,
                include_external=True,
                chunk_filter=external_filter,
            )
            _dedupe_extend(ext_pool, ext_seen, hits)
        ext_pool.sort(key=lambda s: s.score, reverse=True)
        ext_quota = total if mode == ResearchMode.EXTERNAL else max(3, total // 2)
        _dedupe_extend(pooled, seen, ext_pool[:ext_quota])

    if mode != ResearchMode.EXTERNAL:
        # ── 淡江內部池（internal / comparative / 舊行為）────
        include_external_legacy = scope is None and task_type in {"social_research", "social_publicity", "composite"}
        for i, raw in enumerate(queries or []):
            if not raw.strip():
                continue
            hits = hybrid_search(
                index,
                raw,
                k=per_query,
                parsed=parse(raw),
                current_year=current_year,
                min_curated=2 if i == 0 else 0,
                min_archive=1 if i == 0 else 0,
                include_external=include_external_legacy,
                chunk_filter=(None if scope is None else (lambda c: not _is_external_chunk(c))),
            )
            _dedupe_extend(pooled, seen, hits)

    external_first = [s for s in pooled if _is_external_chunk(s.chunk)]
    internal_rest = [s for s in pooled if not _is_external_chunk(s.chunk)]
    internal_rest.sort(key=lambda s: s.score, reverse=True)
    if mode in {ResearchMode.EXTERNAL, ResearchMode.COMPARATIVE}:
        # 外校證據保額——比較分析時不能被內部段落擠光
        keep_internal = max(0, total - len(external_first))
        bundle.hits = external_first + internal_rest[:keep_internal]
    else:
        pooled.sort(key=lambda s: s.score, reverse=True)
        bundle.hits = pooled[:total]

    from ..retrieval import TIER_CURATED

    bundle.curated_count = sum(
        1 for s in bundle.hits if s.chunk.tier >= TIER_CURATED and not _is_external_chunk(s.chunk)
    )
    bundle.external_count = sum(1 for s in bundle.hits if _is_external_chunk(s.chunk))
    bundle.archive_count = len(bundle.hits) - bundle.curated_count - bundle.external_count
    bundle.conflicts = detect_conflicts(bundle.hits, current_facts=term_service.load().known())
    return bundle
