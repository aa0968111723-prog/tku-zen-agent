#!/usr/bin/env python3
"""量測 metadata 規則在真實索引上的覆蓋率。

用法（在 repo 根目錄）：
    python scripts/metadata_coverage.py
    python scripts/metadata_coverage.py --limit-empty 50

本腳本載入 ``app.retrieval.get_index()``，用索引裡每段已經算好的
``ChunkMeta`` 做檔案層 / chunk 層統計，並用與 ``app.rag.metadata._match_first``
相同的最長別名邏輯，重掃路徑 + 來源標籤，計算每條規則、每個別名的命中次數。

不改規則、不寫回索引、不碰 knowledge/ 原始檔。

預期輸出格式範例
-----------------
    === metadata 規則覆蓋率 ===
    產生時間：2026-08-28T12:00:00+08:00
    索引：512 檔 / 2430 段
    來源：社團知識庫, 任務劇本, 歷年檔案, 外校公開參考

    ## 檔案層未命中率（以「至少一段被索引」的路徑計）
    欄位            命中   未命中    未命中率
    activity         259     248     48.9%
    document_type    281     226     44.6%
    team              35     472     93.1%
    academic_year    480      27      5.3%
    semester         210     297     58.6%

    ## chunk 層未命中率
    欄位            命中   未命中    未命中率
    activity        1400    1030     42.4%
    ...

    ## 逐資料夾明細（檔案層）
    資料夾                         檔數  act未中  doc未中  team未中  year未中  sem未中
    雲端文件/114學年度              120      40       30        110        0       55
    雲端文件/營隊                    31      12        8         28        4       31
    ...

    ## 規則命中次數（檔案層；winner = infer() 實際寫入 meta 的值）
    [activity]
      每週社課     90
      期初茶會     55
      （未命中）  248
    [document_type]
      企劃書       80
      ...
    [team]
      總召         20
      （未命中）  472

    ## 別名命中次數（檔案層；路徑或來源標籤含該字串即計 1，可重疊）
    [activity.每週社課]
      社課         90
    [activity.期初茶會]
      期初茶會     40
      茶會         30
      浮花禪光      4
      浮游          1
      紓壓禪        2
      快樂禪        1
    [activity.挑戰營]
      挑戰營       18
      登峰傳心      0     ← 零命中別名
      ...

    ## 三欄全空（activity + document_type + team 皆空）
    共 120 檔。前 50 筆：
      knowledge/雲端文件/109學年度/淡大109帶家日記.md
      ...

對照基線（本次稽核先前實測，不是本腳本輸出）：
    507 檔中 activity 未命中 248、document_type 226、team 472。
    本腳本跑出來的數字若與這組不同，以本腳本當次 stdout 為準。
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FIELDS = ("activity", "document_type", "team", "academic_year", "semester")
EMPTY_TRIPLE = ("activity", "document_type", "team")


# ── 與 app.rag.metadata._match_first 相同的計分（只讀，不改規則）──


def _alias_hits(text: str, rules: tuple) -> dict[str, list[str]]:
    """回傳 {rule_name: [命中的別名, ...]}。大小寫不敏感，一個規則可中多個別名。"""
    low = text.lower()
    found: dict[str, list[str]] = {}
    for name, keywords in rules:
        hits = [kw for kw in keywords if kw.lower() in low]
        if hits:
            found[name] = hits
    return found


def _folder_key(path: Path, knowledge_dir: Path) -> str:
    try:
        rel = path.resolve().relative_to(knowledge_dir.resolve())
    except ValueError:
        return path.parent.name or "(root)"
    parts = rel.parts
    if len(parts) == 1:
        return "(knowledge 根目錄)"
    if parts[0] == "雲端文件" and len(parts) >= 2:
        return f"雲端文件/{parts[1]}"
    return parts[0]


def _is_empty(value: object) -> bool:
    return value is None or value == ""


def _pct(miss: int, total: int) -> str:
    if total <= 0:
        return "n/a"
    return f"{100.0 * miss / total:.1f}%"


def _load_index():
    try:
        from app import retrieval
        from app.rag import metadata as rag_metadata
        from app import config
    except Exception as exc:  # noqa: BLE001
        print("未執行：無法 import app.retrieval / app.rag.metadata。")
        print(f"原因：{type(exc).__name__}: {exc}")
        sys.exit(2)
    try:
        index = retrieval.get_index()
    except Exception as exc:  # noqa: BLE001
        print("未執行：get_index() 失敗。")
        print(f"原因：{type(exc).__name__}: {exc}")
        sys.exit(2)
    return index, rag_metadata, config


def _file_rows(index) -> list[dict]:
    """每個被索引檔案一列。meta 取該檔第一段（路徑推導欄位通常整檔一致）。"""
    first: dict[str, dict] = {}
    chunk_count: Counter[str] = Counter()
    for chunk in index.chunks:
        path = str(chunk.path)
        chunk_count[path] += 1
        if path in first:
            continue
        meta = getattr(chunk, "meta", None)
        first[path] = {
            "path": path,
            "source": getattr(chunk, "source", "") or "",
            "meta": meta,
            "chunks": 0,
        }
    for path, row in first.items():
        row["chunks"] = chunk_count[path]
    return list(first.values())


def _field_value(meta, field: str):
    if meta is None:
        return None
    return getattr(meta, field, None)


def _print_miss_table(title: str, rows_meta: list, label: str) -> None:
    print(f"\n## {title}")
    print(f"{'\u6b04\u4f4d':<16}{'\u547d\u4e2d':>8}{'\u672a\u547d\u4e2d':>8}{'\u672a\u547d\u4e2d\u7387':>10}")
    total = len(rows_meta)
    for field in FIELDS:
        miss = sum(1 for meta in rows_meta if _is_empty(_field_value(meta, field)))
        hit = total - miss
        print(f"{field:<16}{hit:>8}{miss:>8}{_pct(miss, total):>10}")
    print(f"（{label} n={total}）")


def main() -> int:
    parser = argparse.ArgumentParser(description="量測 metadata 規則覆蓋率")
    parser.add_argument("--limit-empty", type=int, default=80, help="三欄全空清單最多列幾筆")
    args = parser.parse_args()

    index, rag_metadata, config = _load_index()
    tz = timezone(timedelta(hours=8))
    now = datetime.now(tz).isoformat(timespec="seconds")
    stats = index.stats() if hasattr(index, "stats") else {}

    print("=== metadata 規則覆蓋率 ===")
    print(f"產生時間：{now}")
    print(f"索引：{stats.get('\u6a94\u6848\u6578', '?')} 檔 / {stats.get('\u6bb5\u843d\u6578', len(index.chunks))} 段")
    sources = stats.get("來源") or []
    print(f"來源：{', '.join(sources) if sources else '（未載明）'}")
    print("執行狀態：已載入 get_index()，數字為本次實跑。")

    files = _file_rows(index)
    file_metas = [row["meta"] for row in files]
    chunk_metas = [getattr(c, "meta", None) for c in index.chunks]

    _print_miss_table("檔案層未命中率（至少一段被索引的路徑）", file_metas, "檔")
    _print_miss_table("chunk 層未命中率", chunk_metas, "段")

    folders: dict[str, list[dict]] = defaultdict(list)
    knowledge_dir = config.KNOWLEDGE_DIR
    for row in files:
        folders[_folder_key(Path(row["path"]), knowledge_dir)].append(row)

    print("\n## 逐資料夾明細（檔案層）")
    print(
        f"{'\u8cc7\u6599\u593e':<28}{'\u6a94\u6578':>6}"
        f"{'act\u672a\u4e2d':>8}{'doc\u672a\u4e2d':>8}{'team\u672a\u4e2d':>9}"
        f"{'year\u672a\u4e2d':>9}{'sem\u672a\u4e2d':>8}"
    )
    for folder in sorted(folders):
        rows = folders[folder]
        n = len(rows)

        def _miss(field: str) -> int:
            return sum(1 for r in rows if _is_empty(_field_value(r["meta"], field)))

        print(
            f"{folder:<28}{n:>6}"
            f"{_miss('activity'):>8}{_miss('document_type'):>8}{_miss('team'):>9}"
            f"{_miss('academic_year'):>9}{_miss('semester'):>8}"
        )

    print("\n## 規則命中次數（檔案層；winner = infer() 寫入 meta 的值）")
    rule_tables = (
        ("activity", rag_metadata.ACTIVITY_RULES),
        ("document_type", rag_metadata.DOC_TYPE_RULES),
        ("team", rag_metadata.TEAM_RULES),
    )
    for field, rules in rule_tables:
        print(f"[{field}]")
        counts: Counter[str] = Counter()
        empty = 0
        for meta in file_metas:
            value = _field_value(meta, field)
            if _is_empty(value):
                empty += 1
            else:
                counts[str(value)] += 1
        for name, _aliases in rules:
            print(f"  {name:<12}{counts.get(name, 0):>6}")
        extras = sorted(set(counts) - {name for name, _ in rules})
        for name in extras:
            print(f"  {name:<12}{counts[name]:>6}  ← meta 有值但不在現行規則表")
        print(f"  {'（未命中）':<12}{empty:>6}")

    print("\n## 別名命中次數（檔案層；路徑或來源標籤含該字串即計 1）")
    print("零命中別名會標 ← 零命中別名")
    for field, rules in rule_tables:
        alias_counter: Counter[tuple[str, str]] = Counter()
        for row in files:
            hay = f"{row['path']} {row['source']}"
            for name, aliases in _alias_hits(hay, rules).items():
                for alias in aliases:
                    alias_counter[(name, alias)] += 1
        for name, aliases in rules:
            print(f"[{field}.{name}]")
            for alias in aliases:
                n = alias_counter.get((name, alias), 0)
                mark = "     ← 零命中別名" if n == 0 else ""
                print(f"  {alias:<16}{n:>6}{mark}")

    empty_files = [
        row
        for row in files
        if all(_is_empty(_field_value(row["meta"], f)) for f in EMPTY_TRIPLE)
    ]
    empty_files.sort(key=lambda r: r["path"])
    print("\n## 三欄全空（activity + document_type + team 皆空）")
    print(f"共 {len(empty_files)} 檔。", end="")
    if empty_files:
        shown = empty_files[: max(0, args.limit_empty)]
        print(f"列出 {len(shown)} 筆：")
        for row in shown:
            try:
                rel = Path(row["path"]).resolve().relative_to(ROOT.resolve())
            except ValueError:
                rel = Path(row["path"])
            print(f"  {rel.as_posix()}")
        if len(empty_files) > len(shown):
            print(f"  … 其餘 {len(empty_files) - len(shown)} 筆省略（--limit-empty 可調）")
    else:
        print()

    print("\n（結束）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
