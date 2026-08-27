"""P0 regression tests for the startup import graph."""

from __future__ import annotations

import subprocess
import sys


def _run(code: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=False,
    )


def test_main_and_public_map_import_without_circular_import():
    result = _run("import app.main; print('app.main import ok')")
    assert result.returncode == 0, result.stderr
    assert "app.main import ok" in result.stdout

    result = _run("from app.research import public_map; print('public_map import ok')")
    assert result.returncode == 0, result.stderr
    assert "public_map import ok" in result.stdout


def test_research_and_rag_package_initializers_are_lightweight():
    result = _run(
        "import sys; import app.research; import app.rag; "
        "assert 'app.rag.context' not in sys.modules; "
        "assert 'app.retrieval' not in sys.modules"
    )
    assert result.returncode == 0, result.stderr


def test_public_map_has_eight_categories_and_https_guard():
    from app.research.public_map import (
        PUBLIC_SOURCE_ENTITIES,
        REQUIRED_CATEGORIES,
        is_fetchable_url,
    )

    assert len(PUBLIC_SOURCE_ENTITIES) == 8
    assert {entity.category for entity in PUBLIC_SOURCE_ENTITIES} == set(REQUIRED_CATEGORIES)
    for entity in PUBLIC_SOURCE_ENTITIES:
        assert entity.official_https
        assert all(is_fetchable_url(url) for url in entity.official_https)
    assert not is_fetchable_url("http://www.tku.edu.tw/")
    assert not is_fetchable_url("https://example.com/")
