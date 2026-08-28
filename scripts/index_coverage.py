#!/usr/bin/env python3
"""對照 knowledge/ 檔案系統與 ``app.retrieval.get_index()`` 實際索引。

用法（在 repo 根目錄）：
    python scripts/index_coverage.py
    python scripts/index_coverage.py --limit 80

本腳本回答兩件 metadata_coverage.py 不負責的事：

1. knowledge/ 裡有哪些 .md 根本沒進 Index.build() 的來源清單
   （例如 ``knowledge/公開來源/`` 不在 retrieval.Index.build 掃描範圍）。
2. 有進來源清單、但切段後 0 chunk 的檔
   （標題-only 或本體 < MIN_BODY_CHARS=40，見 app/retrieval.py）。

不改索引、不刪檔、不推測檔案內容。

預期輸出格式範例
-----------------
    === 索引覆蓋率 ===
    產生時間：2026-08-28T12:00:00+08:00
    knowledge/ 內 .md：530
    Index.build 來源清單：512
    實際產生 chunk 的檔：500
    chunk 數：2430

    ## 來源清單構成
    curated              1
    playbook             8
    archive            500
    external_reference   3
    conversation         0   （ENABLE_LINE_CORPUS=False）

    ## 在 knowledge/ 但不在 Index.build 來源清單
    共 10 檔。
      knowledge/公開來源/淡水與淡江地方資料入口.md
      ...

    ## 在來源清單但 0 chunk（沒被索引成段落）
    共 12 檔。
      knowledge/雲端文件/114學年度/未命名文件.md
      ...

    ## 索引有 chunk 但 knowledge/ 找不到該路徑
    共 0 檔。

    ## 各資料夾：磁碟 / 來源清單 / 有 chunk
    資料夾                         磁碟  來源  有chunk  未進來源  0chunk
    雲端文件/114學年度              130   130     124        0      6
    公開來源                         2     0       0        2      0
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


def _rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _discover_sources(config) -> list[tuple[Path, str]]:
    """與 retrieval.Index.build 相同的檔案來源清單（只列路徑與 source_type）。"""
    files: list[tuple[Path, str]] = []
    kb = config.KNOWLEDGE_DIR / "00_社團知識庫.md"
    if kb.exists():
        files.append((kb, "curated"))
    if config.PLAYBOOK_DIR.exists():
        for p in sorted(config.PLAYBOOK_DIR.glob("*.md")):
            files.append((p, "playbook"))
    if config.DRIVE_DOCS_DIR.exists():
        for p in sorted(config.DRIVE_DOCS_DIR.rglob("*.md")):
            files.append((p, "archive"))
    if config.ENABLE_LINE_CORPUS and config.CORPUS_DIR.exists():
        for p in sorted(config.CORPUS_DIR.glob("*.md")):
            files.append((p, "conversation"))
    if config.EXTERNAL_REFERENCE_DIR.exists():
        for p in sorted(config.EXTERNAL_REFERENCE_DIR.rglob("*.md")):
            files.append((p, "external_reference"))
    return files


def main() -> int:
    parser = argparse.ArgumentParser(description="對照 knowledge/ 與 get_index()")
    parser.add_argument("--limit", type=int, default=80, help="清單最多列幾筆")
    args = parser.parse_args()

    try:
        from app import config, retrieval
    except Exception as exc:  # noqa: BLE001
        print("未執行：無法 import app.retrieval / app.config。")
        print(f"原因：{type(exc).__name__}: {exc}")
        return 2

    try:
        index = retrieval.get_index()
    except Exception as exc:  # noqa: BLE001
        print("未執行：get_index() 失敗。")
        print(f"原因：{type(exc).__name__}: {exc}")
        return 2

    tz = timezone(timedelta(hours=8))
    now = datetime.now(tz).isoformat(timespec="seconds")
    knowledge_dir: Path = config.KNOWLEDGE_DIR

    disk_md = sorted(p for p in knowledge_dir.rglob("*.md") if p.is_file())
    disk_set = {p.resolve() for p in disk_md}

    sources = _discover_sources(config)
    source_set = {p.resolve() for p, _ in sources}
    source_type_by_path = {p.resolve(): kind for p, kind in sources}

    indexed_paths: set[Path] = set()
    chunks_by_path: Counter[Path] = Counter()
    for chunk in index.chunks:
        resolved = Path(chunk.path).resolve()
        indexed_paths.add(resolved)
        chunks_by_path[resolved] += 1

    not_in_sources = sorted(disk_set - source_set)
    in_sources_no_chunk = sorted(source_set - indexed_paths)
    indexed_missing_on_disk = sorted(indexed_paths - disk_set)

    print("=== 索引覆蓋率 ===")
    print(f"產生時間：{now}")
    print(f"knowledge/ 內 .md：{len(disk_md)}")
    print(f"Index.build 來源清單：{len(sources)}")
    print(f"實際產生 chunk 的檔：{len(indexed_paths)}")
    print(f"chunk 數：{len(index.chunks)}")
    print("執行狀態：已載入 get_index()，數字為本次實跑。")

    print("\n## 來源清單構成")
    type_counts = Counter(kind for _, kind in sources)
    for kind in ("curated", "playbook", "archive", "external_reference", "conversation"):
        extra = ""
        if kind == "conversation" and not config.ENABLE_LINE_CORPUS:
            extra = "   （ENABLE_LINE_CORPUS=False）"
        print(f"  {kind:<22}{type_counts.get(kind, 0):>6}{extra}")

    print("\n## 在 knowledge/ 但不在 Index.build 來源清單")
    print(f"共 {len(not_in_sources)} 檔。")
    for path in not_in_sources[: args.limit]:
        print(f"  {_rel(path)}")
    if len(not_in_sources) > args.limit:
        print(f"  … 其餘 {len(not_in_sources) - args.limit} 筆省略")

    print("\n## 在來源清單但 0 chunk（沒被索引成段落）")
    print(f"共 {len(in_sources_no_chunk)} 檔。")
    print("可能原因：標題-only、本體 < MIN_BODY_CHARS、或讀檔失敗。本腳本不打開檔案猜內容。")
    for path in in_sources_no_chunk[: args.limit]:
        kind = source_type_by_path.get(path, "?")
        print(f"  [{kind}] {_rel(path)}")
    if len(in_sources_no_chunk) > args.limit:
        print(f"  … 其餘 {len(in_sources_no_chunk) - args.limit} 筆省略")

    print("\n## 索引有 chunk 但 knowledge/ 找不到該路徑")
    print(f"共 {len(indexed_missing_on_disk)} 檔。")
    for path in indexed_missing_on_disk[: args.limit]:
        print(f"  {path.as_posix()}")
    if len(indexed_missing_on_disk) > args.limit:
        print(f"  … 其餘 {len(indexed_missing_on_disk) - args.limit} 筆省略")

    print("\n## 各資料夾：磁碟 / 來源清單 / 有 chunk")
    grouped: dict[str, dict[str, int]] = defaultdict(lambda: {"disk": 0, "source": 0, "chunked": 0})
    for path in disk_md:
        key = _folder_key(path, knowledge_dir)
        grouped[key]["disk"] += 1
        resolved = path.resolve()
        if resolved in source_set:
            grouped[key]["source"] += 1
        if resolved in indexed_paths:
            grouped[key]["chunked"] += 1

    print(f"{'\u8cc7\u6599\u593e':<28}{'\u78c1\u789f':>6}{'\u4f86\u6e90':>6}{'\u6709chunk':>8}{'\u672a\u9032\u4f86\u6e90':>10}{'0chunk':>8}")
    for key in sorted(grouped):
        row = grouped[key]
        not_src = row["disk"] - row["source"]
        zero = row["source"] - row["chunked"]
        print(
            f"{key:<28}{row['disk']:>6}{row['source']:>6}{row['chunked']:>8}"
            f"{not_src:>10}{zero:>8}"
        )

    print("\n（結束）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
