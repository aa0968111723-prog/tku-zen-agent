#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
python -m compileall -q app tests scripts
python - <<'PY'
import ast
from pathlib import Path
files = list(Path("app").rglob("*.py")) + list(Path("tests").rglob("*.py"))
for path in files:
    ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
print(f"lint-ast parsed {len(files)} python files")
PY
if python -c "import mypy" 2>/dev/null; then
  python -m mypy app tests --ignore-missing-imports
else
  echo "BLOCKED_BY_EXTERNAL_DEPENDENCY: mypy is not a project dependency"
fi
python -m pytest tests --maxfail=20 -ra
if git ls-files '*.sqlite' '*.sqlite3' | grep .; then
  echo "sqlite binaries must not be committed" >&2
  exit 1
fi
echo "ci local checks passed"
