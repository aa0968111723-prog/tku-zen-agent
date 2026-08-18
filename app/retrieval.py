"""社團知識庫的本地檢索（BM25 層）。

刻意不用外部 embedding API：
  · 免費額度有限，每次提問都打 embedding API 太浪費
  · 完全離線，知識庫內容不會為了建索引而先送出去

BM25 對中文專有名詞、活動名稱、歷史檔名特別有效，是整個檢索的骨幹。
語意層（app/rag/semantic.py，本機 LSA）疊在這之上補「講同一件事但用詞不同」
的情況，兩者用 RRF 融合，見 app/rag/hybrid.py。
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from . import config
from .rag import metadata as rag_metadata
from .rag import semantic

_CJK = re.compile(r"[㐀-䶿一-鿿豈-﫿]")
_ASCII_WORD = re.compile(r"[a-zA-Z0-9]+")
_HEADING = re.compile(r"^(#{1,4})\s+(.*)$")

MAX_CHUNK_CHARS = 800
# 本體（扣掉標題行）短於這個長度就不建成段落 —— 純標題的段落會因為
# 標題字面命中而排到前面，但點進去什麼內容都沒有。
MIN_BODY_CHARS = 40
# 這些章節是導覽用的，對每個查詢都會命中，但沒有實質內容。
SKIP_HEADINGS = {"目錄", "目次", "contents", "table of contents"}
# 標題命中比內文命中更能代表段落主旨，而且不受段落長度影響 ——
# 例如「社課主題庫」那一段整段都是課程名稱，內文幾乎不出現「社課」兩字。
HEADING_WEIGHT = 3


# 中文虛詞。兩個字都是虛詞的二元組（「有哪」「可以」「什麼」）不帶語意，
# 但因為不常出現在特定段落裡，IDF 反而很高，會製造假命中 ——
# 「社課有哪些主題可以選」曾因此把〈社課主題庫〉整段擠出結果。
_FUNCTION_CHARS = set(
    "的了是在有我你他她它們這那些什麼哪個誰嗎呢吧啊呀喔哦"
    "可以要會能得着著過就都也不沒很太更最和跟與及並且但而或"
    "上下中內外前後左右對從把被給讓向往到於為以之其此該"
    "來去做說想看用好多少大小新舊己自各每再又才只還如果因所然"
)


def _is_stop_bigram(token: str) -> bool:
    return len(token) == 2 and token[0] in _FUNCTION_CHARS and token[1] in _FUNCTION_CHARS


def tokenize(text: str) -> list[str]:
    """中文切成連續字元的二元組，英數切成單字。"""
    tokens: list[str] = []
    for word in _ASCII_WORD.findall(text.lower()):
        tokens.append(word)
    run: list[str] = []
    for ch in text:
        if _CJK.match(ch):
            run.append(ch)
        else:
            tokens.extend(_bigrams(run))
            run = []
    tokens.extend(_bigrams(run))
    return tokens


def _bigrams(chars: list[str]) -> list[str]:
    if not chars:
        return []
    if len(chars) == 1:
        return chars[:] if chars[0] not in _FUNCTION_CHARS else []
    out = ["".join(chars[i : i + 2]) for i in range(len(chars) - 1)]
    return [t for t in out if not _is_stop_bigram(t)]


# 精選的知識庫與任務劇本是「這件事該怎麼做」，歷年檔案是「以前怎麼做的範例」。
# 兩者都要能查到，但問「該怎麼做」時前者要優先 —— 否則 500 多份歷年檔案的
# 標題會把劇本壓下去（實測：問「社課當天流程時間表」會拿到歷年的「出隊時間表」）。
TIER_CURATED = 1.35
TIER_ARCHIVE = 1.0


@dataclass
class Chunk:
    source: str      # 顯示用的來源名稱，例如「社團知識庫 › 12. 招生方法」
    path: str        # 檔案路徑
    text: str
    heading: str = ""   # 標題路徑，額外加權用
    tier: float = TIER_ARCHIVE
    meta: "object | None" = None   # rag.metadata.ChunkMeta，供 hybrid 重排用

    tokens: Counter = None      # type: ignore[assignment]
    length: int = 0

    def __post_init__(self) -> None:
        counts = Counter(tokenize(self.text))
        # 長度只算本文 —— 標題加權不該讓段落在長度正規化時被多罰
        self.length = sum(counts.values()) or 1
        if self.heading:
            for term, n in Counter(tokenize(self.heading)).items():
                counts[term] += n * HEADING_WEIGHT
        self.tokens = counts


def _split_markdown(
    path: Path, label: str, tier: float = TIER_ARCHIVE, source_type: str = "archive"
) -> list[Chunk]:
    """依標題切段；過長的段落再依空行硬切。

    兩個刻意的取捨：
      · 只有標題、沒有本體的段落不建索引（例如 `## 12. 招生方法` 下面
        直接接 `### 12.1`）。這種段落標題命中率高但完全沒內容，
        會把真正有料的段落擠掉。標題仍會留在路徑上給子段落用。
      · H1 視為文件標題，不進標題路徑 —— label 已經表達同樣的資訊了。
    """
    raw = path.read_text(encoding="utf-8", errors="replace")
    chunks: list[Chunk] = []
    trail: list[str] = []       # 目前的標題路徑（不含 H1）
    buf: list[str] = []
    skipping = False            # 是否在「目錄」這類導覽章節裡

    def flush() -> None:
        block = "\n".join(buf).strip()
        buf.clear()
        if not block or skipping:
            return
        # 扣掉標題行，看還剩多少實質內容
        body = "\n".join(l for l in block.splitlines() if not _HEADING.match(l.strip())).strip()
        if len(body) < MIN_BODY_CHARS:
            return
        heading = " › ".join(t for t in trail if t)
        source = f"{label} › {heading}" if heading else label
        pieces = _hard_split(block)
        for i, piece in enumerate(pieces):
            # 硬切出來的後續片段沒有標題行，補上才看得懂是哪一段
            if i > 0 and heading:
                piece = f"（承上：{heading}）\n\n{piece}"
            chunks.append(
                Chunk(
                    source=source,
                    path=str(path),
                    text=piece,
                    heading=f"{label} {heading}",
                    tier=tier,
                    meta=rag_metadata.infer(path, source, piece, source_type),
                )
            )

    for line in raw.splitlines():
        m = _HEADING.match(line.strip())
        if m:
            flush()
            level = len(m.group(1))
            title = m.group(2).strip()
            skipping = title.strip().lower() in SKIP_HEADINGS
            if level == 1:
                trail = []
            else:
                trail = trail[: level - 2]
                while len(trail) < level - 2:
                    trail.append("")
                trail.append(title)
            buf.append(line)
        else:
            buf.append(line)
    flush()
    return chunks


def _hard_split(body: str) -> list[str]:
    if len(body) <= MAX_CHUNK_CHARS:
        return [body]
    out: list[str] = []
    current: list[str] = []
    size = 0
    for para in body.split("\n\n"):
        if size + len(para) > MAX_CHUNK_CHARS and current:
            out.append("\n\n".join(current))
            current, size = [], 0
        current.append(para)
        size += len(para) + 2
    if current:
        out.append("\n\n".join(current))
    return out


class Index:
    """BM25 + 文件層級加權。

    純 BM25 在這份語料上有個明顯的失效模式：每份劇本開頭的
    「## 什麼時候用這份劇本」是一串觸發關鍵字，又短又密，
    分數會壓過同一份劇本裡真正有內容但比較長的段落，
    甚至把整份相關劇本擠出結果之外。

    解法是先算「哪一份文件整體最相關」，再回頭加權該文件的所有段落。
    B 調低到 0.5 也是同個目的 —— 少罰長段落一點。
    """

    K1 = 1.4
    B = 0.5
    DOC_BOOST = 0.6      # 文件層級加權的強度
    DOC_TOP_N = 3        # 每份文件取前幾高的段落分數來代表整份文件

    def __init__(self) -> None:
        self.chunks: list[Chunk] = []
        self.df: Counter = Counter()
        self.avg_len: float = 1.0
        self.sources: list[str] = []
        self.file_count: int = 0

    def build(self) -> "Index":
        self.chunks = []
        files: list[tuple[Path, str, float, str]] = []

        kb = config.KNOWLEDGE_DIR / "00_社團知識庫.md"
        if kb.exists():
            files.append((kb, "社團知識庫", TIER_CURATED, "curated"))

        if config.PLAYBOOK_DIR.exists():
            for p in sorted(config.PLAYBOOK_DIR.glob("*.md")):
                files.append((p, f"任務劇本／{p.stem}", TIER_CURATED, "playbook"))

        # 共用雲端匯入的歷年文件（企劃書、細流、社評、社課、文案…）。
        # 跑過 scripts/ingest_drive.py 才會有這個資料夾 —— 執行腳本本身就是啟用動作。
        if config.DRIVE_DOCS_DIR.exists():
            for p in sorted(config.DRIVE_DOCS_DIR.rglob("*.md")):
                files.append((p, f"歷年檔案／{p.parent.name}／{p.stem}", TIER_ARCHIVE, "archive"))

        if config.ENABLE_LINE_CORPUS and config.CORPUS_DIR.exists():
            for p in sorted(config.CORPUS_DIR.glob("*.md")):
                files.append((p, f"歷史對話／{p.stem}", TIER_ARCHIVE, "conversation"))

        for path, label, tier, source_type in files:
            try:
                self.chunks.extend(_split_markdown(path, label, tier, source_type))
            except OSError:
                continue

        self.df = Counter()
        total = 0
        for c in self.chunks:
            total += c.length
            for term in c.tokens:
                self.df[term] += 1
        self.avg_len = (total / len(self.chunks)) if self.chunks else 1.0
        self.sources = sorted({label.split("／")[0] for _, label, _, _ in files})
        self.file_count = len(files)

        # 語意層是加分項：建不起來（沒有 scipy、語料太小）就自動降級成純 BM25
        self.fingerprint = f"{len(self.chunks)}:{int(total)}:{self.file_count}"
        semantic.build(self.chunks, tokenize, self.fingerprint)
        return self

    MIN_CURATED_HITS = 2

    def search(self, query: str, k: int = 6, min_curated: int | None = None) -> list[tuple[float, Chunk]]:
        return self.search_scored(query, k, min_curated)[0]

    def search_scored(
        self, query: str, k: int = 6, min_curated: int | None = None
    ) -> tuple[list[tuple[float, Chunk]], float]:
        """回傳 (結果, 信心值)。

        信心值刻意用**未加權**的原始 BM25 最高分去算 —— 層級加權與文件加權
        會把分數整體墊高，段落數變多也會，用加權後的分數當門檻會一直漂移。
        """
        if not self.chunks:
            return [], 0.0
        q_terms = Counter(tokenize(query))
        if not q_terms:
            return [], 0.0
        n = len(self.chunks)
        scored: list[tuple[float, Chunk]] = []
        for c in self.chunks:
            length = c.length
            score = 0.0
            for term, qf in q_terms.items():
                tf = c.tokens.get(term)
                if not tf:
                    continue
                idf = math.log(1 + (n - self.df[term] + 0.5) / (self.df[term] + 0.5))
                denom = tf + self.K1 * (1 - self.B + self.B * length / self.avg_len)
                score += idf * (tf * (self.K1 + 1) / denom) * min(qf, 3)
            if score > 0:
                scored.append((score, c))

        if not scored:
            return [], 0.0

        raw_top = max(s for s, _ in scored)
        n_terms = max(1, len(q_terms))
        confidence = raw_top / math.sqrt(n_terms)

        # 文件層級加權
        per_doc: dict[str, list[float]] = {}
        for score, c in scored:
            per_doc.setdefault(c.path, []).append(score)
        doc_score = {p: sum(sorted(v, reverse=True)[: self.DOC_TOP_N]) for p, v in per_doc.items()}
        best_doc = max(doc_score.values()) or 1.0

        boosted = [
            (score * c.tier * (1 + self.DOC_BOOST * doc_score[c.path] / best_doc), c)
            for score, c in scored
        ]
        boosted.sort(key=lambda x: x[0], reverse=True)
        hits = self._ensure_curated(
            boosted, k, self.MIN_CURATED_HITS if min_curated is None else min_curated
        )
        return hits, confidence

    @staticmethod
    def _ensure_curated(
        ranked: list[tuple[float, Chunk]], k: int, min_curated: int
    ) -> list[tuple[float, Chunk]]:
        """保證結果裡至少有幾段精選內容。

        加進 500 多份歷年檔案之後，「社課」「招生」這些詞在語料裡到處都是，
        IDF 幾乎歸零，反而是歷年檔名裡的特殊詞（「出隊時間表」）變成鑑別詞，
        把真正該回答問題的劇本整個擠出結果。

        與其一直調權重，不如直接保留名額：代理每次都同時看到
        「這件事該怎麼做」（劇本）與「以前怎麼做的」（歷年檔案）。
        """
        top = ranked[:k]
        if min_curated <= 0:
            return top

        curated_in_top = [x for x in top if x[1].tier >= TIER_CURATED]
        if len(curated_in_top) >= min_curated:
            return top

        need = min_curated - len(curated_in_top)
        extra = [x for x in ranked[k:] if x[1].tier >= TIER_CURATED][:need]
        if not extra:
            return top

        archive_in_top = [x for x in top if x[1].tier < TIER_CURATED]
        keep_archive = archive_in_top[: max(0, k - len(curated_in_top) - len(extra))]
        merged = curated_in_top + keep_archive + extra
        merged.sort(key=lambda x: x[0], reverse=True)
        return merged

    # 信心門檻：原始 BM25 最高分 ÷ √查詢詞數。
    # BM25 永遠會回傳分數最高的段落，就算查詢完全不相干也一樣；問句越長，
    # 累積分數越高 —— 所以要先除掉長度效應再比較。
    #
    # 門檻刻意設得低。匯入 500 多份歷年檔案之後，語料的主題範圍變得很廣
    # （社課簡報談過國際議題、台積電、電影、AI…），已經沒有一個乾淨的分界
    # 能區分「社團有沒有涵蓋這個主題」—— 實測合理查詢低到 5.6，
    # 不相干查詢卻可以高到 9.9，因為它真的命中了談過那個主題的簡報。
    # 所以這裡只擋「完全查不到東西」的情況，剩下的靠 search_knowledge
    # 每次都附上的提醒（歷年檔案是往年資料、不可當今年事實）來把關。
    LOW_CONFIDENCE = 3.0

    def stats(self) -> dict:
        sem = semantic.get()
        return {
            "段落數": len(self.chunks),
            "檔案數": self.file_count,
            "來源": self.sources,
            "已載入雲端文件": config.DRIVE_DOCS_DIR.exists(),
            "已載入LINE語料": config.ENABLE_LINE_CORPUS,
            "語意層": (
                f"LSA {sem.doc_vectors.shape[1]} 維"  # type: ignore[union-attr]
                if sem is not None
                else f"未啟用（{semantic.unavailable_reason() or '尚未建立'}）"
            ),
        }


_index: Index | None = None


def get_index(rebuild: bool = False) -> Index:
    global _index
    if _index is None or rebuild:
        if rebuild:
            semantic.reset()
        _index = Index().build()
    return _index


def build_context(queries: list[str], *, task_type: str = ""):
    """對外的 hybrid 檢索入口（orchestrator 用）。

    放在這裡而不是直接 import app.rag，是為了讓 app.rag 可以反過來
    import app.retrieval 而不會循環。
    """
    from .rag.context import build_context as _build

    return _build(queries, task_type=task_type)
