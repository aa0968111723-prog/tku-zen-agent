"""把「淡大領共用雲端」的文件轉成代理可檢索的知識。

從 Google Drive 下載的 zip 直接串流讀取，不解壓 —— 那 14GB 裡九成是照片與影片，
只有 docx / xlsx / pptx / pdf / odt 這些文件對代理有用。

用法（在專案根目錄執行）：
    python scripts/ingest_drive.py --dry-run          # 先看會處理什麼
    python scripts/ingest_drive.py                    # 實際匯入
    python scripts/ingest_drive.py --src "C:\\path"    # 指定 zip 所在資料夾
    python scripts/ingest_drive.py --only 114 社評     # 只處理路徑含這些字的

## 個資處理

社團雲端裡有大量同學的個人資料（通訊錄、接引名單、簽到表、報名表回覆、社員生日）。
這些檔案**只會抽出欄位標題與筆數，不會抽出任何一列內容** —— 代理需要知道
「招生進度表長什麼樣子」，不需要知道誰報了名。

其餘文件會全文擷取，並自動遮蔽電話、市話、電子郵件、身分證字號、9 碼學號。
"""

from __future__ import annotations

import argparse
import io
import re
import sys
import zipfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except Exception:  # noqa: BLE001
        pass

DEFAULT_SRC = Path(r"C:\Users\User\Downloads")
DEST = ROOT / "knowledge" / "雲端文件"

DOC_EXT = {".docx", ".xlsx", ".pptx", ".pdf", ".odt", ".txt", ".md", ".csv"}
# 舊版二進位格式（.doc/.ppt/.xls）沒有純 Python 的可靠解析器，跳過並列在報告裡
LEGACY_EXT = {".doc", ".ppt", ".xls"}

# 檔名符合這些字樣 = 含同學個資，只抽欄位結構
PII_PATTERNS = re.compile(
    r"(名單|通訊錄|簽到|簽退|點名|出席|回覆|回應|responses|生日|保費|"
    r"聯絡表|聯絡名單|接引狀況|社員狀況|新人&社員|分家|認證名單|總名單)",
    re.I,
)

# 光靠檔名擋不住 —— 例如「招生進度表.xlsx」名字看不出來，裡面卻是一整份同學名冊。
# 所以再依欄位標題判斷：出現任何一個「強指標」，或兩個以上「弱指標」，
# 該工作表就只抽欄位結構。
# 「姓名」自己就算強指標 —— 表格裡有一欄叫姓名，那就是名冊，沒有別的可能。
# （原本把它列為弱指標，結果標題是「層級|姓名|出席確認」的名單整份漏出去。）
PII_COL_STRONG = (
    "姓名", "名字", "名子", "電話", "手機", "信箱", "email", "e-mail", "mail",
    "學號", "身分證", "緊急聯絡", "地址", "line id", "lineid", "有緣人", "被接引人",
)
PII_COL_WEAK = ("系級", "科系", "年級", "生日", "性別", "暱稱", "ig", "line", "班級")


def headers_look_like_roster(headers: list[str]) -> bool:
    joined = " ".join(headers).lower()
    if any(k in joined for k in PII_COL_STRONG):
        return True
    return sum(1 for k in PII_COL_WEAK if k in joined) >= 2


# 第二道防線：有些工作表根本沒有標題列，第一列就是人名資料
# （例如以個接人命名的「柏能」「安倢」分頁，近千筆同學的姓名系級手機與私人筆記）。
# 標題檢查對這種完全無效，所以再看內容長什麼樣子。
_CONTACTISH = re.compile(r"09\d{2}[-\s]?\d{3}[-\s]?\d{3}|[\w.+-]+@[\w-]+\.[\w.]+|\b\d{9}\b")
_CLASSISH = re.compile(
    r"大[一二三四五]|[一二三四五]年級|"
    r"(?:資工|資管|企管|經濟|中文|英文|會計|統計|土木|化工|機電|大傳|教科|運管|財金|"
    r"保險|公行|國企|資圖|日文|法文|西語|俄文|歷史|數學|物理|化學|建築|電機|水環|"
    r"航太|管科|教設|海科|產經|觀光|資訊|法律|師培|體育|音樂|美術)\s?[一二三四五1-5][A-Za-z]?\b"
)
ROSTER_ROW_RATIO = 0.3
ROSTER_MIN_ROWS = 5


def rows_look_like_roster(rows: list[str]) -> bool:
    """抽樣看資料列：夠多列帶著聯絡方式或系級，就是名冊。"""
    sample = [r for r in rows[:40] if r.strip()]
    if len(sample) < ROSTER_MIN_ROWS:
        return False
    hits = sum(1 for r in sample if _CONTACTISH.search(r) or _CLASSISH.search(r))
    return hits / len(sample) >= ROSTER_ROW_RATIO

# 這些是設計檔（海報、名牌、文宣），抽出來的文字沒有意義
DESIGN_HINT = re.compile(r"(海報|名牌|文宣|傳單|三折頁|背面|正面|logo|封面)", re.I)

MIN_CHARS = 150          # 少於這個字數視為設計檔或空白文件
MIN_CHARS_STRUCTURE = 60  # 只抽欄位結構的檔案本來就短，門檻要放寬
MAX_CHARS = 60_000       # 單檔上限，超過截斷（避免一份 PPT 洗掉整個索引）

REDACTIONS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), "［信箱］"),
    (re.compile(r"\b09\d{2}[-\s]?\d{3}[-\s]?\d{3}\b"), "［手機］"),
    (re.compile(r"\b0[2-8][-\s]?\d{3,4}[-\s]?\d{4}\b"), "［市話］"),
    (re.compile(r"\b[A-Z][12]\d{8}\b"), "［身分證］"),
    (re.compile(r"\b\d{9}\b"), "［學號］"),
]


def redact(text: str) -> str:
    for pattern, replacement in REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


def squeeze(text: str) -> str:
    """壓掉多餘空白 —— PPT 與 PDF 抽出來的文字常常一堆空行。"""
    text = re.sub(r"[ \t\u3000]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return "\n".join(line.strip() for line in text.splitlines()).strip()


# ── 各格式的文字擷取 ──────────────────────────────────────────

def _row_cells(row) -> list[str]:  # noqa: ANN001
    cells = [c.text.strip().replace("\n", " ") for c in row.cells]
    # 合併儲存格會讓同一列重複出現，去掉連續重複
    deduped: list[str] = []
    for c in cells:
        if not deduped or deduped[-1] != c:
            deduped.append(c)
    return deduped


def from_docx(data: bytes, force_roster: bool) -> tuple[str, str]:
    """docx 的表格一樣要防名冊 —— 期初活動名單.docx 就是一份含系級電話的完整名冊。"""
    from docx import Document

    doc = Document(io.BytesIO(data))
    parts = [p.text for p in doc.paragraphs if p.text.strip()]
    redacted = 0

    for table in doc.tables:
        rows = list(table.rows)
        if not rows:
            continue
        header = _row_cells(rows[0])
        body = [" | ".join(c) for c in (_row_cells(r) for r in rows[1:]) if any(c)]

        if force_roster or headers_look_like_roster(header) or rows_look_like_roster(body):
            redacted += 1
            parts.append(
                f"（表格：{' | '.join(header) or '這個表格沒有標題列'}　"
                f"共 {len(rows) - 1} 列。這是名冊類表格，只保留欄位結構，內容未擷取。）"
            )
            continue

        parts.append(" | ".join(header))
        parts.extend(body)

    note = f"含 {redacted} 個名冊表格（僅欄位結構）" if redacted else ""
    return "\n".join(parts), note


def from_xlsx(data: bytes, headers_only: bool) -> tuple[str, str]:
    """回傳 (文字, 備註)。

    headers_only 來自檔名判斷；另外每個工作表還會再依欄位標題自行判斷一次，
    只要看起來是名冊就只抽欄位結構 —— 寧可少抽，不要把同學個資送出去。
    """
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    out: list[str] = []
    redacted_sheets = 0
    total_sheets = 0
    try:
        for ws in wb.worksheets:
            total_sheets += 1
            rows = ws.iter_rows(values_only=True)
            try:
                header = next(rows)
            except StopIteration:
                continue
            header_txt = [str(c).strip() for c in header if c is not None and str(c).strip()]
            if len(header_txt) < 2:
                # 有些表第一列是大標題，往下找真正的欄位列
                for candidate in rows:
                    vals = [str(c).strip() for c in candidate if c is not None and str(c).strip()]
                    if len(vals) >= 2:
                        header_txt = vals
                        break

            # 先把資料列讀進來，才能同時做標題檢查與內容檢查
            body: list[str] = []
            truncated = False
            for row in ws.iter_rows(min_row=2, values_only=True):
                vals = [("" if c is None else str(c).strip()) for c in row]
                while vals and not vals[-1]:
                    vals.pop()
                if any(vals):
                    body.append(" | ".join(vals))
                if len(body) >= 300:
                    truncated = True
                    break

            if headers_only or headers_look_like_roster(header_txt) or rows_look_like_roster(body):
                redacted_sheets += 1
                n = sum(1 for _ in ws.iter_rows(min_row=2, values_only=True))
                out.append(
                    f"### 工作表：{ws.title}\n"
                    f"欄位：{' | '.join(header_txt) or '（這份表沒有標題列）'}\n"
                    f"（共 {n} 筆資料。這是名冊類資料，只保留欄位結構，內容未擷取。）"
                )
                continue

            out.append(f"### 工作表：{ws.title}")
            if header_txt:
                out.append("欄位：" + " | ".join(header_txt))
            out.extend(body)
            if truncated:
                out.append("…（列數過多，其餘略）")
    finally:
        wb.close()

    if redacted_sheets == total_sheets and total_sheets:
        note = "僅欄位結構"
    elif redacted_sheets:
        note = f"部分僅欄位結構（{redacted_sheets}/{total_sheets} 個工作表）"
    else:
        note = ""
    return "\n".join(out), note


def from_pptx(data: bytes) -> str:
    from pptx import Presentation

    prs = Presentation(io.BytesIO(data))
    out: list[str] = []
    for i, slide in enumerate(prs.slides, 1):
        texts: list[str] = []
        for shape in slide.shapes:
            if shape.has_text_frame:
                t = shape.text_frame.text.strip()
                if t:
                    texts.append(t)
            if getattr(shape, "has_table", False):
                for row in shape.table.rows:
                    cells = [c.text.strip() for c in row.cells]
                    if any(cells):
                        texts.append(" | ".join(cells))
        if texts:
            out.append(f"### 第 {i} 頁\n" + "\n".join(texts))
    return "\n\n".join(out)


def from_pdf(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    out: list[str] = []
    for i, page in enumerate(reader.pages, 1):
        if i > 120:
            out.append("…（頁數過多，其餘略）")
            break
        try:
            t = (page.extract_text() or "").strip()
        except Exception:  # noqa: BLE001 —— 單頁解析失敗不該讓整份檔案失敗
            continue
        if t:
            out.append(t)
    return "\n\n".join(out)


def from_odt(data: bytes) -> str:
    import xml.etree.ElementTree as ET

    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        xml = zf.read("content.xml")
    root = ET.fromstring(xml)
    texts = [t.strip() for t in root.itertext() if t and t.strip()]
    return "\n".join(texts)


def from_plain(data: bytes) -> str:
    for enc in ("utf-8", "utf-8-sig", "cp950", "big5"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


# ── 分類與輸出 ────────────────────────────────────────────────

def category_of(path: str) -> str:
    """依照雲端的資料夾結構決定歸類。"""
    parts = path.split("/")
    if len(parts) < 2:
        return "其他"
    top = parts[1]
    if re.fullmatch(r"\d{3}", top):
        return f"{top}學年度"
    if "挑戰營" in top or "寒假" in top:
        return "營隊"
    if top in {"開示", "講師資訊", "音樂區"}:
        return top
    return "其他"


def safe_name(path: str) -> str:
    stem = Path(path).stem
    stem = re.sub(r'[<>:"/\\|?*]', "_", stem).strip(" .")
    return (stem or "未命名")[:110]


def extract(name: str, data: bytes) -> tuple[str, str]:
    """回傳 (文字, 備註)。"""
    ext = Path(name).suffix.lower()
    headers_only = bool(PII_PATTERNS.search(Path(name).name))

    if ext == ".docx":
        return from_docx(data, headers_only)
    if ext == ".xlsx":
        return from_xlsx(data, headers_only)
    if ext == ".pptx":
        return from_pptx(data), ""
    if ext == ".pdf":
        return from_pdf(data), ""
    if ext == ".odt":
        return from_odt(data), ""
    if ext in {".txt", ".md", ".csv"}:
        return from_plain(data), ""
    return "", "不支援的格式"


def main() -> int:
    ap = argparse.ArgumentParser(description="把共用雲端的文件匯入代理知識庫")
    ap.add_argument("--src", type=Path, default=DEFAULT_SRC, help="zip 檔所在資料夾")
    ap.add_argument("--pattern", default="淡大領共用雲端-*.zip", help="zip 檔名樣式")
    ap.add_argument("--only", nargs="*", default=None, help="只處理路徑包含這些關鍵字的檔案")
    ap.add_argument("--dry-run", action="store_true", help="只顯示會處理什麼，不寫檔")
    args = ap.parse_args()

    zips = sorted(args.src.glob(args.pattern))
    if not zips:
        print(f"在 {args.src} 找不到符合 {args.pattern} 的檔案。")
        return 1

    print(f"來源：{len(zips)} 個 zip @ {args.src}")
    print(f"目的：{DEST}")
    print("─" * 70)

    if not args.dry_run:
        DEST.mkdir(parents=True, exist_ok=True)

    stats: Counter = Counter()
    skipped: list[tuple[str, str]] = []
    written_names: set[str] = set()
    total_chars = 0

    for zpath in zips:
        with zipfile.ZipFile(zpath) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                name = info.filename
                ext = Path(name).suffix.lower()

                if ext in LEGACY_EXT:
                    stats["跳過_舊格式"] += 1
                    skipped.append((name, "舊版二進位格式，無法解析"))
                    continue
                if ext not in DOC_EXT:
                    stats["跳過_非文件"] += 1
                    continue
                if args.only and not any(k in name for k in args.only):
                    stats["跳過_不符篩選"] += 1
                    continue
                if info.file_size > 300 * 1024 * 1024:
                    stats["跳過_過大"] += 1
                    skipped.append((name, f"檔案過大 {info.file_size/1024/1024:.0f}MB"))
                    continue

                try:
                    data = zf.read(info)
                    text, note = extract(name, data)
                except Exception as exc:  # noqa: BLE001
                    stats["跳過_解析失敗"] += 1
                    skipped.append((name, f"{type(exc).__name__}: {exc}"))
                    continue

                text = squeeze(redact(text))
                if len(text) > MAX_CHARS:
                    text = text[:MAX_CHARS] + "\n\n…（內容過長已截斷）"

                floor = MIN_CHARS_STRUCTURE if "僅欄位結構" in note else MIN_CHARS
                if len(text) < floor:
                    reason = "設計檔或掃描檔，抽不到文字" if DESIGN_HINT.search(name) else f"文字太少（{len(text)} 字）"
                    stats["跳過_內容太少"] += 1
                    skipped.append((name, reason))
                    continue

                if note:
                    stats["含名冊_已遮蔽"] += 1

                category = category_of(name)
                base = safe_name(name)
                out_name = f"{base}.md"
                n = 2
                while (category, out_name) in written_names:
                    out_name = f"{base}_{n}.md"
                    n += 1
                written_names.add((category, out_name))  # type: ignore[arg-type]

                header = [
                    f"# {Path(name).stem}",
                    "",
                    f"> 來源：`{name}`",
                    f"> 分類：{category}",
                ]
                if note:
                    header.append(
                        f"> **{note}** —— 偵測到同學個人資料欄位，名冊內容未擷取，只保留欄位設計。"
                    )
                header.extend(["> 這是歷史檔案，不代表現況。引用具體數字或日期前請先確認。", "", "---", ""])

                stats[f"匯入_{ext}"] += 1
                total_chars += len(text)

                if not args.dry_run:
                    folder = DEST / category
                    folder.mkdir(parents=True, exist_ok=True)
                    (folder / out_name).write_text("\n".join(header) + text + "\n", encoding="utf-8")

    print("\n── 結果 ──")
    imported = sum(v for k, v in stats.items() if k.startswith("匯入_"))
    for key in sorted(k for k in stats if k.startswith("匯入_")):
        print(f"  {key.replace('匯入_', '匯入 '):<14} {stats[key]:>4} 份")
    print(f"  {'合計':<14} {imported:>4} 份，約 {total_chars/10000:.1f} 萬字")
    print(f"  其中 {stats['含名冊_已遮蔽']} 份偵測到名冊欄位，只保留欄位結構、未擷取內容")

    print("\n── 跳過 ──")
    for key in sorted(k for k in stats if k.startswith("跳過_")):
        print(f"  {key.replace('跳過_', ''):<14} {stats[key]:>4}")

    interesting = [s for s in skipped if "非文件" not in s[1]]
    if interesting:
        print(f"\n── 跳過明細（前 40 / 共 {len(interesting)}）──")
        for name, reason in interesting[:40]:
            print(f"  {Path(name).name[:52]:<54} {reason}")

    if args.dry_run:
        print("\n（試跑模式，沒有寫任何檔案。拿掉 --dry-run 才會實際匯入。）")
    else:
        print(f"\n完成。檔案寫在 {DEST}")
        print("下一步：重新啟動伺服器，或呼叫 POST /api/reindex 重建索引。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
