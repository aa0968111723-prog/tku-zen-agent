"""本機語意檢索（LSA / 潛在語意分析）。

為什麼不用 transformer embedding：
  · sentence-transformers 要拉 torch，安裝約 2GB。對一個要在幹部的
    Windows 筆電上一鍵啟動、還要塞進 Zeabur 容器的工具來說太重。
  · 外部 embedding API 會吃掉本來就不多的免費額度，而且等於把知識庫
    整批送出去。

LSA 用 TF-IDF 矩陣的截斷 SVD，抓的是「詞的共現結構」。它補得到 BM25
補不到的地方：字面沒重疊但講同一件事的段落（「怎麼招新生」↔「接引流程」）。
純 numpy/scipy，離線，建索引幾秒鐘。

這一層是**加分項不是必需品**：scipy 沒裝、或語料太小撐不起 SVD 時，
會自動降級成純 BM25，不會讓檢索壞掉。
"""

from __future__ import annotations

import math
import threading
from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..retrieval import Chunk

# 潛在維度。太小抓不到結構，太大等於在學雜訊；這個語料規模 192 夠用。
N_COMPONENTS = 192
MIN_CHUNKS_FOR_SVD = 60
MIN_DF = 2          # 只出現在一個段落的詞對語意結構沒貢獻，砍掉可大幅縮小矩陣


@dataclass
class SemanticIndex:
    vocab: dict[str, int]
    idf: "object"          # np.ndarray
    doc_vectors: "object"  # np.ndarray (n_chunks, k)，已 L2 正規化
    components: "object"   # np.ndarray (k, n_terms)
    n_chunks: int

    def query(self, text: str, tokenize) -> "object | None":
        import numpy as np

        counts = Counter(t for t in tokenize(text) if t in self.vocab)
        if not counts:
            return None
        vec = np.zeros(len(self.vocab), dtype=np.float32)
        for term, tf in counts.items():
            idx = self.vocab[term]
            vec[idx] = (1.0 + math.log(tf)) * self.idf[idx]
        norm = np.linalg.norm(vec)
        if norm == 0:
            return None
        vec /= norm
        projected = self.components @ vec
        pnorm = np.linalg.norm(projected)
        if pnorm == 0:
            return None
        return projected / pnorm

    def similarities(self, query_vec) -> "object":
        return self.doc_vectors @ query_vec


_index: SemanticIndex | None = None
_fingerprint: str = ""
_lock = threading.RLock()
_unavailable_reason = ""


def unavailable_reason() -> str:
    return _unavailable_reason


def build(chunks: list["Chunk"], tokenize, fingerprint: str) -> SemanticIndex | None:
    """建語意索引。失敗就回 None，呼叫端自動降級成純 BM25。"""
    global _index, _fingerprint, _unavailable_reason

    with _lock:
        if _index is not None and _fingerprint == fingerprint:
            return _index

        if len(chunks) < MIN_CHUNKS_FOR_SVD:
            _unavailable_reason = f"段落數 {len(chunks)} 太少，撐不起 SVD"
            _index, _fingerprint = None, fingerprint
            return None

        try:
            import numpy as np
            from scipy.sparse import csr_matrix
            from scipy.sparse.linalg import svds
        except ImportError as exc:
            _unavailable_reason = f"缺少 numpy/scipy（{exc}），語意層停用"
            _index, _fingerprint = None, fingerprint
            return None

        try:
            # 1. 詞彙表（砍掉只出現一次的詞）
            df: Counter = Counter()
            tokenized: list[Counter] = []
            for c in chunks:
                counts = Counter(tokenize(c.text))
                tokenized.append(counts)
                df.update(counts.keys())

            vocab = {t: i for i, t in enumerate((t for t, n in df.items() if n >= MIN_DF))}
            if len(vocab) < N_COMPONENTS * 2:
                _unavailable_reason = f"詞彙量 {len(vocab)} 太少"
                _index, _fingerprint = None, fingerprint
                return None

            n_docs = len(chunks)
            idf = np.zeros(len(vocab), dtype=np.float32)
            for term, i in vocab.items():
                idf[i] = math.log((1 + n_docs) / (1 + df[term])) + 1.0

            # 2. 稀疏 TF-IDF
            rows: list[int] = []
            cols: list[int] = []
            vals: list[float] = []
            for r, counts in enumerate(tokenized):
                buf: list[tuple[int, float]] = []
                for term, tf in counts.items():
                    i = vocab.get(term)
                    if i is None:
                        continue
                    buf.append((i, (1.0 + math.log(tf)) * idf[i]))
                if not buf:
                    continue
                norm = math.sqrt(sum(v * v for _, v in buf)) or 1.0
                for i, v in buf:
                    rows.append(r)
                    cols.append(i)
                    vals.append(v / norm)

            matrix = csr_matrix(
                (np.asarray(vals, dtype=np.float32), (rows, cols)),
                shape=(n_docs, len(vocab)),
            )

            k = min(N_COMPONENTS, min(matrix.shape) - 1)
            if k < 8:
                _unavailable_reason = "矩陣太小"
                _index, _fingerprint = None, fingerprint
                return None

            # 3. 截斷 SVD
            u, s, vt = svds(matrix, k=k)
            order = np.argsort(-s)
            u, s, vt = u[:, order], s[order], vt[order, :]

            doc_vectors = (u * s).astype(np.float32)
            norms = np.linalg.norm(doc_vectors, axis=1, keepdims=True)
            norms[norms == 0] = 1.0
            doc_vectors /= norms

            _index = SemanticIndex(
                vocab=vocab,
                idf=idf,
                doc_vectors=doc_vectors,
                components=vt.astype(np.float32),
                n_chunks=n_docs,
            )
            _fingerprint = fingerprint
            _unavailable_reason = ""
            return _index
        except Exception as exc:  # noqa: BLE001 —— 語意層是加分項，壞了也不能拖垮檢索
            _unavailable_reason = f"建立語意索引失敗：{type(exc).__name__}: {exc}"
            _index, _fingerprint = None, fingerprint
            return None


def get() -> SemanticIndex | None:
    return _index


def reset() -> None:
    global _index, _fingerprint
    with _lock:
        _index, _fingerprint = None, ""
