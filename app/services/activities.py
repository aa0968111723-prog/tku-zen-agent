"""活動營運領域邏輯：資料驗證、缺口、逾期與可供產出的活動摘要。"""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from typing import Any

from .session_store import SessionStore

ACTIVE_ACTIVITY_KEY = "__active_activity_id__"

ACTIVITY_STATUS_ALIASES = {
    "規劃中": "planning", "籌備中": "planning", "planning": "planning",
    "進行中": "active", "active": "active",
    "已完成": "completed", "完成": "completed", "completed": "completed",
    "已取消": "cancelled", "取消": "cancelled", "cancelled": "cancelled",
    "暫停": "paused", "paused": "paused",
}
TASK_STATUS_ALIASES = {
    "待辦": "pending", "尚未開始": "pending", "pending": "pending",
    "進行中": "in_progress", "處理中": "in_progress", "in_progress": "in_progress",
    "卡住": "blocked", "blocked": "blocked",
    "完成": "completed", "已完成": "completed", "completed": "completed",
    "取消": "cancelled", "已取消": "cancelled", "cancelled": "cancelled",
}
PRIORITY_ALIASES = {
    "低": "low", "low": "low", "一般": "normal", "普通": "normal", "normal": "normal",
    "高": "high", "high": "high", "緊急": "urgent", "urgent": "urgent",
}

FIELD_LABELS = {
    "name": "活動名稱", "activity_type": "活動類型", "academic_year": "學年度", "semester": "學期",
    "start_at": "日期時間", "end_at": "結束時間", "location": "地點", "goal": "活動目標",
    "audience": "參與對象", "signup_url": "報名方式", "owner": "總負責人", "notes": "備註",
}

OUTPUT_REQUIREMENTS = {
    "status": ("name", "start_at", "location", "owner"),
    "plan": ("name", "activity_type", "academic_year", "semester", "start_at", "location", "goal", "audience", "owner"),
    "publicity": ("name", "start_at", "location", "audience", "signup_url"),
    "slides": ("name", "start_at", "location", "goal", "audience", "owner"),
    "minutes": ("name", "start_at", "owner"),
}

OUTPUT_ALIASES = {
    "": "status", "狀態": "status", "status": "status",
    "企劃": "plan", "企劃書": "plan", "plan": "plan",
    "網宣": "publicity", "貼文": "publicity", "輪播": "publicity", "reels": "publicity", "publicity": "publicity",
    "簡報": "slides", "投影片": "slides", "slides": "slides",
    "會議紀錄": "minutes", "minutes": "minutes",
}
PLACEHOLDERS = {"待填", "未定", "待確認", "不知道", "unknown", "tbd", "—", "-"}


def _is_missing(value: Any) -> bool:
    text = str(value or "").strip()
    return not text or text.lower() in PLACEHOLDERS


def public_activity(activity: dict[str, Any]) -> dict[str, Any]:
    """給前端或模型的活動資料，不暴露內部 user_id。"""
    return {key: value for key, value in activity.items() if key != "user_id"}


def normalize_activity_status(value: str) -> str:
    normalized = ACTIVITY_STATUS_ALIASES.get((value or "planning").strip().lower())
    if not normalized:
        raise ValueError("活動狀態只支援：規劃中、進行中、暫停、已完成、已取消")
    return normalized


def normalize_task_status(value: str) -> str:
    normalized = TASK_STATUS_ALIASES.get((value or "pending").strip().lower())
    if not normalized:
        raise ValueError("工作狀態只支援：待辦、進行中、卡住、完成、取消")
    return normalized


def normalize_priority(value: str) -> str:
    normalized = PRIORITY_ALIASES.get((value or "normal").strip().lower())
    if not normalized:
        raise ValueError("優先級只支援：低、一般、高、緊急")
    return normalized


def validate_temporal(value: str, label: str) -> str:
    """接受 ISO 日期或日期時間；空字串代表尚未確認。"""
    value = (value or "").strip()
    if not value:
        return ""
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} 請使用 YYYY-MM-DD 或 ISO 日期時間") from exc
    return value


def _as_datetime(value: str) -> datetime | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        parsed = (
            datetime.combine(date.fromisoformat(value), time.max)
            if len(value) == 10 else datetime.fromisoformat(value.replace("Z", "+00:00"))
        )
    except ValueError:
        try:
            parsed = datetime.combine(date.fromisoformat(value), time.max)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def resolve_activity(
    store: SessionStore,
    user_id: str,
    *,
    activity_id: str = "",
    activity_name: str = "",
    project_id: str | None = None,
) -> dict[str, Any] | None:
    if activity_id:
        return store.get_activity(activity_id, user_id)
    records = store.list_activities(user_id, project_id=project_id, limit=100)
    if activity_name:
        wanted = activity_name.strip().lower()
        exact = [item for item in records if item["name"].strip().lower() == wanted]
        if exact:
            return exact[0]
        partial = [item for item in records if wanted in item["name"].lower() or item["name"].lower() in wanted]
        if partial:
            return partial[0]
        return None
    if not records:
        return None
    # 無參數 fallback 不能默默拿上學期活動（稽核不可靠 #34）：
    # 先挑本學期的活動；本學期沒有才退回最近更新的那筆。
    from . import current_term as term_service

    current_year = str(term_service.load().get("academic_year") or "")
    if current_year:
        current = [r for r in records if str(r.get("academic_year") or "") == current_year]
        if current:
            return current[0]
    return records[0]


def activate(store: SessionStore, project_id: str | None, activity_id: str) -> None:
    if project_id:
        store.remember(project_id, ACTIVE_ACTIVITY_KEY, activity_id, source="activity")


def readiness_report(
    activity: dict[str, Any],
    tasks: list[dict[str, Any]],
    *,
    output_type: str = "status",
    now: datetime | None = None,
) -> dict[str, Any]:
    output_key = OUTPUT_ALIASES.get((output_type or "status").strip().lower(), "status")
    requirements = OUTPUT_REQUIREMENTS[output_key]
    missing = [
        {"key": key, "label": FIELD_LABELS[key], "placeholder": "待填"}
        for key in requirements if _is_missing(activity.get(key))
    ]
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    remaining = [task for task in tasks if task.get("status") not in {"completed", "cancelled"}]
    completed = [task for task in tasks if task.get("status") == "completed"]
    unassigned = [task for task in remaining if _is_missing(task.get("assignee"))]
    overdue: list[dict[str, Any]] = []
    future: list[tuple[datetime, dict[str, Any]]] = []
    for task in remaining:
        deadline = _as_datetime(str(task.get("due_at") or ""))
        if not deadline:
            continue
        if deadline < current:
            overdue.append(task)
        else:
            future.append((deadline, task))
    future.sort(key=lambda item: item[0])
    assignments = [
        {
            "task_id": task["id"], "title": task["title"], "group": task.get("group_name") or "未分組",
            "assignee": task.get("assignee") or "待指派", "due_at": task.get("due_at") or "待排",
            "status": task.get("status") or "pending", "priority": task.get("priority") or "normal",
        }
        for task in tasks
    ]
    return {
        "activity": activity,
        "output_type": output_key,
        "ready": not missing and not overdue and not unassigned,
        "missing_fields": missing,
        "counts": {
            "total": len(tasks), "completed": len(completed), "remaining": len(remaining),
            "overdue": len(overdue), "unassigned": len(unassigned),
        },
        "overdue_tasks": overdue,
        "unassigned_tasks": unassigned,
        "remaining_tasks": remaining,
        "assignments": assignments,
        "next_deadline": future[0][1] if future else None,
    }


def brief_markdown(report: dict[str, Any]) -> str:
    activity = report["activity"]
    missing = "、".join(item["label"] for item in report["missing_fields"]) or "無"
    counts = report["counts"]
    lines = [
        "## 已確認的活動資料",
        f"- 活動 ID：{activity['id']}",
        f"- 名稱：{activity.get('name') or '待填'}",
        f"- 類型：{activity.get('activity_type') or '待填'}",
        f"- 學期：{activity.get('academic_year') or '待填'} {activity.get('semester') or ''}".rstrip(),
        f"- 日期時間：{activity.get('start_at') or '待填'}",
        f"- 地點：{activity.get('location') or '待填'}",
        f"- 目標：{activity.get('goal') or '待填'}",
        f"- 對象：{activity.get('audience') or '待填'}",
        f"- 報名方式：{activity.get('signup_url') or '待填'}",
        f"- 總負責人：{activity.get('owner') or '待填'}",
        f"- 產出前待填：{missing}",
        f"- 工作統計：共 {counts['total']}、完成 {counts['completed']}、剩餘 {counts['remaining']}、逾期 {counts['overdue']}、未指派 {counts['unassigned']}",
    ]
    if report["assignments"]:
        lines += ["", "### 分工與待辦"]
        lines.extend(
            f"- [{item['status']}] {item['title']}｜{item['group']}｜{item['assignee']}｜期限 {item['due_at']}"
            for item in report["assignments"]
        )
    return "\n".join(lines)


def project_activity_context(store: SessionStore, user_id: str, project_id: str) -> str:
    memory = store.recall(project_id)
    active_id = (memory.get(ACTIVE_ACTIVITY_KEY) or {}).get("value", "")
    activity = resolve_activity(store, user_id, activity_id=active_id, project_id=project_id)
    if not activity:
        return ""
    tasks = store.list_activity_tasks(activity["id"], user_id)
    return brief_markdown(readiness_report(activity, tasks))
