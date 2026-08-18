"""把 LINE 匯出的聊天記錄整理成代理可以檢索的語料。

用法（在專案根目錄執行）：
    python scripts/ingest_line.py                       # 從 D:\\對話 匯入
    python scripts/ingest_line.py --src "D:\\某資料夾"
    python scripts/ingest_line.py --dry-run             # 只看會處理什麼，不寫檔
    python scripts/ingest_line.py --only 網宣 幹部       # 只匯入檔名含這些字的

匯入時會自動遮蔽：手機、市話、電子郵件、身分證字號、9 碼學號、網址的查詢字串。
**人名不會遮蔽** —— 因為對話內容要能看懂誰在講什麼。所以：
  · 匯入後這些內容會在你提問時被送到 NVIDIA 的伺服器
  · 預設是關閉的，要在 .env 把 ENABLE_LINE_CORPUS 設成 true 才會生效
  · 覺得不妥就不要匯入，代理光靠知識庫與劇本也能運作
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DEFAULT_SRC = Path(r"D:\對話")
DEST = ROOT / "knowledge" / "語料"

# ── 解析 ──────────────────────────────────────────────────────
DATE_LINE = re.compile(r"^(\d{4})[./-](\d{1,2})[./-](\d{1,2})(?:\s+(星期.|週.))?\s*$")
MSG_LINE = re.compile(r"^(\d{1,2}:\d{2})[\t ]+(.*)$")

# ── 遮蔽 ──────────────────────────────────────────────────────
REDACTIONS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), "［信箱］"),
    (re.compile(r"\b09\d{2}[-\s]?\d{3}[-\s]?\d{3}\b"), "［手機］"),
    (re.compile(r"\b0[2-8][-\s]?\d{3,4}[-\s]?\d{4}\b"), "［市話］"),
    (re.compile(r"\b[A-Z][12]\d{8}\b"), "［身分證］"),
    (re.compile(r"\b\d{9}\b"), "［學號］"),
    (re.compile(r"(https?://[^\s?]+)\?[^\s]*"), r"\1"),  # 網址保留，砍掉帶 token 的查詢字串
]

# 這些行沒有內容價值，丟掉
NOISE = re.compile(
    r"^(?:\[貼圖\]|\[照片\]|\[影片\]|\[檔案\]|\[語音訊息\]|\[禮物\]|"
    r"貼圖|照片|影片|已收回訊息|通話時間 .*|未接來電|取消通話|"
    r".*已加入群組。?$|.*已離開群組。?$|.*已新增.*至群組。?$|"
    r".*收回了訊息。?$|.*邀請.*加入群組。?$)$"
)


def redact(text: str) -> str:
    for pattern, replacement in REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


def parse(path: Path) -> tuple[list[tuple[str, str, str, str]], Counter]:
    """回傳 [(日期, 時間, 說話者, 內容)] 與統計。"""
    stats: Counter = Counter()
    rows: list[tuple[str, str, str, str]] = []
    current_date = "（未知日期）"

    raw = path.read_text(encoding="utf-8", errors="replace")
    for line in raw.splitlines():
        line = line.rstrip()
        if not line.strip():
            continue

        m = DATE_LINE.match(line)
        if m:
            current_date = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
            continue

        m = MSG_LINE.match(line)
        if m:
            time_str, rest = m.group(1), m.group(2).strip()
            if not rest:
                continue
            parts = rest.split(" ", 1)
            if len(parts) == 2:
                speaker, body = parts[0], parts[1].strip()
            else:
                speaker, body = "（系統）", parts[0]
            if NOISE.match(body):
                stats["略過雜訊"] += 1
                continue
            rows.append((current_date, time_str, speaker, redact(body)))
            stats["訊息"] += 1
            continue

        # 沒有時間戳 → 上一則訊息的續行
        if rows:
            d, t, s, body = rows[-1]
            rows[-1] = (d, t, s, body + "\n" + redact(line.strip()))
            stats["續行"] += 1
        else:
            stats["略過開頭雜訊"] += 1

    return rows, stats


def to_markdown(title: str, rows: list[tuple[str, str, str, str]]) -> str:
    out = [
        f"# 歷史對話：{title}",
        "",
        "> 由 LINE 匯出記錄自動整理。手機、市話、信箱、身分證、學號已遮蔽。",
        "> 這是歷史紀錄，不代表現況 —— 引用前請確認資訊是否仍然有效。",
        "",
    ]
    last_date = None
    for date, time_str, speaker, body in rows:
        if date != last_date:
            out.append(f"\n## {date}\n")
            last_date = date
        indented = body.replace("\n", "\n  ")
        out.append(f"- **{time_str} {speaker}**：{indented}")
    return "\n".join(out) + "\n"


def clean_title(name: str) -> str:
    name = re.sub(r"^\[LINE\]\s*", "", name)
    return re.sub(r'[<>:"/\\|?*]', "_", name).strip() or "未命名"


def main() -> int:
    ap = argparse.ArgumentParser(description="把 LINE 匯出記錄整理成代理語料")
    ap.add_argument("--src", type=Path, default=DEFAULT_SRC, help="LINE 匯出檔所在資料夾")
    ap.add_argument("--only", nargs="*", default=None, help="只處理檔名包含這些關鍵字的檔案")
    ap.add_argument("--dry-run", action="store_true", help="只顯示會處理什麼，不寫檔")
    ap.add_argument("--min-messages", type=int, default=20, help="訊息數少於這個值就跳過")
    args = ap.parse_args()

    if not args.src.exists():
        print(f"找不到來源資料夾：{args.src}")
        return 1

    files = sorted(p for p in args.src.glob("*.txt") if p.is_file())
    if args.only:
        files = [p for p in files if any(k in p.name for k in args.only)]
    if not files:
        print(f"{args.src} 裡沒有符合的 .txt 檔。")
        return 1

    if not args.dry_run:
        DEST.mkdir(parents=True, exist_ok=True)

    total_msgs = 0
    written = 0
    print(f"來源：{args.src}\n目的：{DEST}\n{'─' * 58}")

    for path in files:
        rows, stats = parse(path)
        title = clean_title(path.stem)

        if len(rows) < args.min_messages:
            print(f"  跳過  {title}（只有 {len(rows)} 則）")
            continue

        total_msgs += len(rows)
        dates = sorted({r[0] for r in rows if r[0] != "（未知日期）"})
        span = f"{dates[0]} ~ {dates[-1]}" if dates else "無日期"
        print(f"  匯入  {title}：{len(rows)} 則　{span}")

        if not args.dry_run:
            (DEST / f"{title}.md").write_text(to_markdown(title, rows), encoding="utf-8")
            written += 1

    print("─" * 58)
    if args.dry_run:
        print(f"（試跑）共 {len(files)} 個檔案、{total_msgs} 則訊息。加上 --dry-run 以外的參數才會實際寫檔。")
    else:
        print(f"完成：寫出 {written} 個語料檔，共 {total_msgs} 則訊息。")
        print("\n下一步：")
        print("  1. 到 knowledge/語料/ 隨手翻幾份，確認沒有不該外流的內容")
        print("  2. 把 .env 的 ENABLE_LINE_CORPUS 改成 true")
        print("  3. 重新啟動伺服器（或呼叫 POST /api/reindex）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
