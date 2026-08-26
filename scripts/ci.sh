#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
python -m compileall -q app tests scripts
python -m pytest tests --maxfail=8
if git ls-files '*.sqlite' '*.sqlite3' | grep .; then
  echo "sqlite binaries must not be committed" >&2
  exit 1
fi
echo "ci local checks passed"
