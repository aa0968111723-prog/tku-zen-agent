"""`python -m app` 啟動伺服器。"""

from __future__ import annotations

import os
import secrets
import sys
import webbrowser

import uvicorn

from . import config


def _fix_windows_console() -> None:
    """Windows 主控台預設是 cp950，印中文或符號會直接丟 UnicodeEncodeError
    把伺服器打掛。改成 UTF-8，並且遇到印不出來的字元也只是替換掉、不中斷。"""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.kernel32.SetConsoleOutputCP(65001)  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001
            pass


def _abort_missing_access_token() -> None:
    """部署環境沒設授權碼就不啟動——沒有它等於把整個代理公開給任何人。

    這是刻意的 fail-fast，但容器平台會一直重啟，訊息很容易被 BackOff 洗掉，
    所以把「該做什麼」整段印在 stdout（Zeabur 之類的平台 runtime log 常常
    只收得到 stdout），stderr 只留一行摘要，避免同一份記錄裡出現兩次。
    """
    summary = "[錯誤] 部署或 token 認證模式必須設定 APP_ACCESS_TOKEN，服務已停止。"
    suggestion = secrets.token_urlsafe(18)
    lines = [
        "─" * 62,
        summary,
        "",
        "  沒有授權碼的話，任何拿到網址的人都能用這個代理、消耗 NVIDIA 額度，",
        "  還看得到彼此的對話與檔案，所以這裡直接拒絕啟動。",
        "",
        "  解法（以 Zeabur 為例，其他平台同理）：",
        "    1. 服務頁面 → Variables／環境變數",
        "    2. 新增 APP_ACCESS_TOKEN，值填一組只有幹部知道的授權碼",
        f"       例如可以直接用這組隨機產生的：{suggestion}",
        "    3. 順手加 ADMIN_ACCESS_TOKEN（管理端另一組，別跟上面一樣）",
        "    4. Redeploy／重啟服務",
        "",
        "  設定值請勿包含前後引號，貼上時只要授權碼本身。",
        "  只在自己電腦單人使用、不對外開放時，才可以留空（本機模式）。",
        "─" * 62,
    ]
    print("\n".join(lines), flush=True)
    print(summary, file=sys.stderr, flush=True)
    sys.exit(1)


def main() -> None:
    _fix_windows_console()
    # 部署到 Zeabur / Railway 之類的平台時，平台會用 PORT 環境變數指定連接埠
    port = int(os.getenv("PORT") or config.PORT)
    host = os.getenv("HOST") or config.HOST
    on_server = bool(
        os.getenv("ZEABUR")
        or os.getenv("RAILWAY_ENVIRONMENT")
        or os.getenv("PORT")
        or host == "0.0.0.0"
    )
    if (on_server or config.AUTH_MODE == "token") and not config.APP_ACCESS_TOKEN:
        _abort_missing_access_token()
    if on_server:
        host = "0.0.0.0"  # noqa: S104 —— 部署環境必須綁所有介面

    problems = config.missing_config()
    print("─" * 62)
    print("  淡江大學領袖禪學社 · 專屬 AI 代理")
    print("─" * 62)
    print(f"  模型　　：{config.NVIDIA_MODEL}")
    print(f"  產出落點：{config.DEFAULT_DESTINATION}")
    print(f"  產出資料夾：{config.OUTPUT_DIR}")
    for p in problems:
        print(f"  [!] {p}")
    print(f"\n  請用瀏覽器打開： http://{'127.0.0.1' if host == '0.0.0.0' else host}:{port}\n")  # noqa: S104
    print("  要停止：在這個視窗按 Ctrl+C")
    print("─" * 62)

    if not on_server:
        try:
            webbrowser.open(f"http://{host}:{port}")
        except Exception:  # noqa: BLE001 —— 開不了瀏覽器不影響伺服器
            pass

    uvicorn.run("app.main:app", host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
