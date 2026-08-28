"""遠端 PostgreSQL 型別/約束的邊界契約。

本機 SQLite 每一欄都是 TEXT 且沒有 CHECK，所以「寫得進本機」完全不代表
「送得進 InsForge」。這組測試把 migrations/insforge/*.sql 的真實約束搬進
測試裡，確保 payload 在離開行程前就已經符合遠端型別。

對應的四個真實缺陷（都會讓整批 50 筆 upsert 失敗）：
  * entities.type = 'date'      → CHECK IN (person/club/school/event/scene/place/object)
  * events.date/time = ""       → date / time
  * sources.captured_at = 奈秒字串 → timestamptz
  * review_queue.reviewed_at = "" → timestamptz
"""

from __future__ import annotations

import re
from datetime import datetime

import pytest

from app.services.data_organization import ENTITY_TYPES, _entity_type
from app.services.insforge_data_sync import _conform_to_postgres, _pg_date, _pg_time, _pg_timestamp
from app.services.insforge_sync import _vector, _vector_strict


# --- 把真實 DDL 約束寫成可執行的驗證器 ---------------------------------

def _is_timestamptz(value) -> bool:
    if value is None:
        return True
    try:
        datetime.fromisoformat(str(value))
        return True
    except ValueError:
        return False


def _is_date(value) -> bool:
    return value is None or bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(value)))


def _is_time(value) -> bool:
    return value is None or bool(re.fullmatch(r"\d{2}:\d{2}:\d{2}", str(value)))


def _is_nullable_fk(value) -> bool:
    """遠端是 text REFERENCES ...：可以是 NULL，但不能是空字串。"""
    return value is None or str(value) != ""


DDL = {
    "sources": {"captured_at": _is_timestamptz},
    "entities": {"type": lambda v: v in ENTITY_TYPES, "source_id": _is_nullable_fk},
    "events": {"date": _is_date, "time": _is_time, "club_id": _is_nullable_fk, "school_id": _is_nullable_fk},
    "review_queue": {"reviewed_at": _is_timestamptz},
}


def assert_conforms(table: str, payload: dict) -> None:
    for column, check in DDL[table].items():
        if column in payload:
            assert check(payload[column]), f"{table}.{column}={payload[column]!r} 不符合遠端型別"


# --- 缺陷重現：未經處理的本機列違反遠端約束 ----------------------------

RAW_ROWS = {
    "entities": {"id": "e1", "type": "date", "name": "2026-03-05", "source_id": "", "owner_id": "u"},
    "events": {"id": "v1", "name": "期初茶會", "date": "", "time": "", "club_id": "", "school_id": "", "owner_id": "u"},
    "sources": {"id": "s1", "captured_at": "1755500000000000000", "owner_id": "u"},
    "review_queue": {"id": "r1", "asset_id": "a1", "reviewed_at": "", "owner_id": "u"},
}


@pytest.mark.parametrize("table", sorted(RAW_ROWS))
def test_raw_local_rows_violate_remote_constraints(table):
    """紅燈證明：這正是 2026-08-26 之後第一次 organization sync 會炸掉的原因。"""
    with pytest.raises(AssertionError):
        assert_conforms(table, dict(RAW_ROWS[table]))


@pytest.mark.parametrize("table", sorted(RAW_ROWS))
def test_conformed_rows_satisfy_remote_constraints(table):
    """綠燈：經過 _conform_to_postgres 之後每一欄都符合真實 DDL。"""
    assert_conforms(table, _conform_to_postgres(table, dict(RAW_ROWS[table])))


# --- 個別轉換器 ---------------------------------------------------------

def test_nanosecond_timestamp_becomes_iso():
    result = _pg_timestamp("1755500000000000000")
    assert result is not None and datetime.fromisoformat(result).year == 2025


def test_blank_and_none_timestamps_become_null():
    assert _pg_timestamp("") is None
    assert _pg_timestamp(None) is None
    assert _pg_date("") is None
    assert _pg_time("") is None


def test_iso_timestamp_passes_through():
    assert _pg_timestamp("2026-08-26T03:27:51+00:00") == "2026-08-26T03:27:51+00:00"


def test_time_is_normalised_and_garbage_rejected():
    assert _pg_time("9:05") == "09:05:00"
    assert _pg_time("14:30:15") == "14:30:15"
    assert _pg_time("25:00") is None
    assert _pg_time("下午三點") is None


def test_entity_type_normalisation_covers_the_date_case():
    assert _entity_type("date") == "event"
    assert _entity_type("person") == "person"
    assert _entity_type("完全沒看過的型別") == "object"
    for kind in ENTITY_TYPES:
        assert _entity_type(kind) == kind


def test_conform_never_nulls_a_not_null_column():
    """entity_relationships.source_id 與 data_lineage.source_id 是 NOT NULL DEFAULT ''。"""
    for table in ("entity_relationships", "data_lineage"):
        assert _conform_to_postgres(table, {"source_id": ""})["source_id"] == ""


# --- 向量維度 -----------------------------------------------------------

def test_vector_strict_rejects_wrong_dimensions():
    """真實 embedding 供應商回 768/1024/3072 維時必須立刻失敗，不能默默補零。"""
    for wrong in (768, 1024, 3072):
        with pytest.raises(ValueError):
            _vector_strict([0.1] * wrong)


def test_vector_strict_accepts_exactly_1536():
    assert len(_vector_strict([0.1] * 1536)) == 1536


def test_lenient_vector_still_pads_for_legacy_visual_path():
    """視覺色彩直方圖是真的 48 維，舊路徑必須維持寬鬆。"""
    assert len(_vector([0.1] * 48, size=1536)) == 1536
