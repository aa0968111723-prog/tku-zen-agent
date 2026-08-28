"""開機冒煙測試：整包 app 一定要 import 得起來。

2026-08-27 線上當機就是這一類錯。某次合併把 ``app/research/__init__.py`` 的
``from . import public_map`` 併進 main，但 ``public_map.py`` 本身沒被加進去，
於是容器一啟動就炸在 import 階段：

    ImportError: cannot import name 'public_map' from partially initialized
    module 'app.research' (most likely due to a circular import)

那句 "circular import" 是 Python 的誤導——真正的原因是**檔案根本不存在**。
而且它發生在 import 期，FastAPI 連建都還沒建起來，平台只能每十秒 BackOff
重啟一次，整個服務等於全掛。

所以這裡守兩件事：
  1. app 底下每一個模組都要 import 得起來，包含只有執行期才會被叫到、
     不在任何測試 import 路徑上的模組（例如各種 tools）。
  2. 相對 import 指到的兄弟模組，檔案必須真的在 repo 裡。
     這條在第 1 條之前就會爆，錯誤訊息直接講出「少了哪個檔」。
"""

from __future__ import annotations

import ast
import importlib
import pkgutil
import traceback
from pathlib import Path

import app

APP_DIR = Path(app.__file__).resolve().parent


def _module_names() -> list[str]:
    return sorted(m.name for m in pkgutil.walk_packages(app.__path__, prefix="app."))


def test_every_app_module_imports() -> None:
    """每個模組單獨 import 都要成功——這是部署前最後一道防線。"""
    failures: list[str] = []
    for name in _module_names():
        try:
            importlib.import_module(name)
        except BaseException:  # noqa: BLE001 —— SystemExit 也算開不起來
            failures.append(f"{name}\n{traceback.format_exc()}")

    assert not failures, "以下模組 import 失敗，部署後會直接 crash：\n\n" + "\n".join(failures)


def test_app_main_imports_like_the_server_does() -> None:
    """``uvicorn app.main:app`` 走的就是這條路徑，單獨釘住它。"""
    main = importlib.import_module("app.main")
    assert main.app is not None


def _relative_targets(tree: ast.Module, source: Path) -> list[tuple[int, str, Path]]:
    """列出這個檔案用相對 import 指到的『模組』，以及它應該在的路徑。

    只檢查模組本身存不存在，不檢查模組裡的名字——名字由第 1 條測試負責，
    這裡刻意保守，避免誤報。
    """
    package_parts = source.parent.relative_to(APP_DIR.parent).parts
    targets: list[tuple[int, str, Path]] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or not node.level:
            continue

        # level=1 是所在套件，level=2 是上一層，依此類推。
        # 非 __init__.py 的模組，level=1 指的是它的所在套件（即父目錄）。
        trim = node.level - 1
        base = package_parts[: len(package_parts) - trim] if trim else package_parts
        if not base:
            continue

        if node.module:
            # from .public_map import X / from ..research.claims import X
            candidates = [(node.module, base + tuple(node.module.split(".")))]
        else:
            # from . import public_map —— 每個名字都必須是子模組
            candidates = [(alias.name, base + (alias.name,)) for alias in node.names]

        for label, parts in candidates:
            root = APP_DIR.parent
            module_file = root.joinpath(*parts).with_suffix(".py")
            package_init = root.joinpath(*parts, "__init__.py")
            if not module_file.exists() and not package_init.exists():
                targets.append((node.lineno, label, module_file))

    return targets


def test_relative_imports_point_at_files_that_exist() -> None:
    """宣告要 import 的模組，檔案必須真的被 commit 進來。

    專治「__init__.py 改了、模組本身忘了加」這種合併漏檔——線上那次當機
    就是這樣來的，而 Python 只會回一句誤導人的 circular import。
    """
    missing: list[str] = []
    for source in sorted(APP_DIR.rglob("*.py")):
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        for lineno, label, expected in _relative_targets(tree, source):
            rel = source.relative_to(APP_DIR.parent)
            missing.append(f"{rel}:{lineno} 匯入 {label!r}，但找不到 {expected.relative_to(APP_DIR.parent)}")

    assert not missing, "相對 import 指到不存在的模組（檔案漏了沒進 repo？）：\n  " + "\n  ".join(missing)
