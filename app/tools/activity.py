"""活動、分工與待辦工具。所有讀寫都透過 RequestContext 驗證使用者歸屬。"""

from __future__ import annotations

from typing import Any

from ..services import activities as domain
from ..services import context as ctx_mod
from ..services import current_term
from ..services.session_store import get_store


def _ctx():
    ctx = ctx_mod.current()
    if ctx is None:
        raise RuntimeError("活動操作需要登入後的工作情境")
    return ctx


def _resolve(activity_id: str = "", activity_name: str = "") -> tuple[Any, dict[str, Any] | None]:
    ctx = _ctx()
    store = get_store()
    activity = domain.resolve_activity(
        store, ctx.user_id, activity_id=activity_id, activity_name=activity_name, project_id=ctx.project_id,
    )
    return ctx, activity


def _result(activity: dict[str, Any], *, output_type: str = "status", message: str = "") -> dict[str, Any]:
    ctx = _ctx()
    store = get_store()
    tasks = store.list_activity_tasks(activity["id"], ctx.user_id)
    report = domain.readiness_report(activity, tasks, output_type=output_type)
    domain.activate(store, ctx.project_id or activity.get("project_id"), activity["id"])
    return {
        "ok": True,
        "message": message or f"已讀取活動「{activity['name']}」：剩餘 {report['counts']['remaining']} 項、逾期 {report['counts']['overdue']} 項、待填 {len(report['missing_fields'])} 項。",
        "activity": domain.public_activity(activity),
        "activity_id": activity["id"],
        "activity_brief": domain.brief_markdown(report),
        "readiness": {key: value for key, value in report.items() if key != "activity"},
    }


def create_activity(
    name: str,
    activity_type: str = "",
    academic_year: str = "",
    semester: str = "",
    start_at: str = "",
    end_at: str = "",
    location: str = "",
    goal: str = "",
    audience: str = "",
    signup_url: str = "",
    owner: str = "",
    status: str = "planning",
    notes: str = "",
    tasks: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if not (name or "").strip():
        return {"ok": False, "code": "missing_arguments", "message": "請提供活動名稱"}
    try:
        normalized_status = domain.normalize_activity_status(status)
        start_at = domain.validate_temporal(start_at, "活動開始時間")
        end_at = domain.validate_temporal(end_at, "活動結束時間")
    except ValueError as exc:
        return {"ok": False, "code": "invalid_arguments", "message": str(exc)}
    ctx = _ctx()
    store = get_store()
    term = current_term.load().known()
    existing = domain.resolve_activity(store, ctx.user_id, activity_name=name, project_id=ctx.project_id)
    values = {
        "activity_type": activity_type, "academic_year": academic_year or term.get("academic_year", ""),
        "semester": semester or term.get("semester", ""), "start_at": start_at, "end_at": end_at,
        "location": location, "goal": goal, "audience": audience, "signup_url": signup_url,
        "owner": owner, "status": normalized_status, "notes": notes,
    }
    if existing and existing["name"].strip().lower() == name.strip().lower():
        updates = {key: value for key, value in values.items() if value and not existing.get(key)}
        activity = store.update_activity(existing["id"], ctx.user_id, updates) or existing
        message = f"活動「{activity['name']}」已存在，已沿用同一筆資料並補上新欄位。"
    else:
        activity = store.create_activity(
            ctx.user_id, name.strip(), project_id=ctx.project_id, **values,
        )
        message = f"已建立活動「{activity['name']}」。"
    added: list[dict[str, Any]] = []
    task_errors: list[str] = []
    for item in tasks or []:
        if not isinstance(item, dict) or not str(item.get("title") or "").strip():
            task_errors.append("有一項工作缺少 title，未新增")
            continue
        task_result = add_activity_task(
            activity_id=activity["id"], title=str(item["title"]),
            group_name=str(item.get("group_name") or ""), assignee=str(item.get("assignee") or ""),
            due_at=str(item.get("due_at") or ""), status=str(item.get("status") or "pending"),
            priority=str(item.get("priority") or "normal"), notes=str(item.get("notes") or ""),
        )
        if task_result.get("ok") and task_result.get("task"):
            added.append(task_result["task"])
        elif not task_result.get("ok"):
            task_errors.append(str(task_result.get("message") or "工作新增失敗"))
    result = _result(activity, message=message + (f" 已建立 {len(added)} 項分工。" if added else ""))
    result["tasks_added"] = added
    result["task_errors"] = task_errors
    return result


def update_activity(
    activity_id: str = "",
    activity_name: str = "",
    name: str | None = None,
    activity_type: str | None = None,
    academic_year: str | None = None,
    semester: str | None = None,
    start_at: str | None = None,
    end_at: str | None = None,
    location: str | None = None,
    goal: str | None = None,
    audience: str | None = None,
    signup_url: str | None = None,
    owner: str | None = None,
    status: str | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    ctx, activity = _resolve(activity_id, activity_name)
    if not activity:
        return {"ok": False, "code": "activity_not_found", "message": "找不到指定活動；可先列出活動或建立新活動。"}
    fields = {
        "name": name, "activity_type": activity_type, "academic_year": academic_year, "semester": semester,
        "start_at": start_at, "end_at": end_at, "location": location, "goal": goal, "audience": audience,
        "signup_url": signup_url, "owner": owner, "status": status, "notes": notes,
    }
    try:
        if fields["status"] is not None:
            fields["status"] = domain.normalize_activity_status(str(fields["status"]))
        if fields["start_at"] is not None:
            fields["start_at"] = domain.validate_temporal(str(fields["start_at"]), "活動開始時間")
        if fields["end_at"] is not None:
            fields["end_at"] = domain.validate_temporal(str(fields["end_at"]), "活動結束時間")
    except ValueError as exc:
        return {"ok": False, "code": "invalid_arguments", "message": str(exc)}
    updated = get_store().update_activity(activity["id"], ctx.user_id, fields)
    return _result(updated or activity, message=f"已更新活動「{(updated or activity)['name']}」。")


def add_activity_task(
    title: str,
    activity_id: str = "",
    activity_name: str = "",
    group_name: str = "",
    assignee: str = "",
    due_at: str = "",
    status: str = "pending",
    priority: str = "normal",
    notes: str = "",
) -> dict[str, Any]:
    ctx, activity = _resolve(activity_id, activity_name)
    if not activity:
        return {"ok": False, "code": "activity_not_found", "message": "找不到指定活動；請先建立活動。"}
    if not (title or "").strip():
        return {"ok": False, "code": "missing_arguments", "message": "請提供工作項目名稱"}
    try:
        due_at = domain.validate_temporal(due_at, "工作期限")
        normalized_status = domain.normalize_task_status(status)
        normalized_priority = domain.normalize_priority(priority)
    except ValueError as exc:
        return {"ok": False, "code": "invalid_arguments", "message": str(exc)}
    store = get_store()
    duplicate = next(
        (task for task in store.list_activity_tasks(activity["id"], ctx.user_id) if task["title"].strip().lower() == title.strip().lower()),
        None,
    )
    if duplicate:
        return {
            **_result(activity, message=f"工作「{duplicate['title']}」已存在，未重複新增。"),
            "task": duplicate,
            "deduplicated": True,
        }
    task = store.create_activity_task(
        ctx.user_id, activity["id"], title.strip(), group_name=group_name, assignee=assignee,
        due_at=due_at, status=normalized_status, priority=normalized_priority, notes=notes,
    )
    return {**_result(activity, message=f"已新增工作「{task['title']}」。"), "task": task}


def update_activity_task(
    task_id: str,
    title: str | None = None,
    group_name: str | None = None,
    assignee: str | None = None,
    due_at: str | None = None,
    status: str | None = None,
    priority: str | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    ctx = _ctx()
    store = get_store()
    current = store.get_activity_task(task_id, ctx.user_id)
    if not current:
        return {"ok": False, "code": "activity_task_not_found", "message": "找不到這項工作，或它不屬於目前使用者。"}
    fields = {
        "title": title, "group_name": group_name, "assignee": assignee, "due_at": due_at,
        "status": status, "priority": priority, "notes": notes,
    }
    try:
        if fields["due_at"] is not None:
            fields["due_at"] = domain.validate_temporal(str(fields["due_at"]), "工作期限")
        if fields["status"] is not None:
            fields["status"] = domain.normalize_task_status(str(fields["status"]))
        if fields["priority"] is not None:
            fields["priority"] = domain.normalize_priority(str(fields["priority"]))
    except ValueError as exc:
        return {"ok": False, "code": "invalid_arguments", "message": str(exc)}
    updated = store.update_activity_task(task_id, ctx.user_id, fields) or current
    activity = store.get_activity(updated["activity_id"], ctx.user_id)
    return {**_result(activity, message=f"已更新工作「{updated['title']}」。"), "task": updated}


def get_activity_status(
    activity_id: str = "", activity_name: str = "", output_type: str = "status",
) -> dict[str, Any]:
    _ctx_value, activity = _resolve(activity_id, activity_name)
    if not activity:
        return {
            "ok": False, "code": "activity_not_found",
            "message": "目前找不到活動資料。可以先建立活動，或提供活動名稱。",
            "alternative": "若只需要草稿，可先標示日期、地點、負責人與報名方式為待填。",
        }
    return _result(activity, output_type=output_type)


def list_activities(status: str = "", semester: str = "", activity_type: str = "", limit: int = 20) -> dict[str, Any]:
    ctx = _ctx()
    try:
        normalized = domain.normalize_activity_status(status) if status else ""
    except ValueError as exc:
        return {"ok": False, "code": "invalid_arguments", "message": str(exc)}
    records = get_store().list_activities(
        ctx.user_id, status=normalized, semester=semester, activity_type=activity_type, limit=limit,
    )
    return {
        "ok": True,
        "message": f"找到 {len(records)} 場活動。" if records else "目前沒有符合條件的活動。",
        "activities": [
            {
                "activity_id": item["id"], "name": item["name"], "activity_type": item["activity_type"],
                "semester": f"{item['academic_year']} {item['semester']}".strip(), "status": item["status"],
                "start_at": item["start_at"] or "待填", "location": item["location"] or "待填",
                "owner": item["owner"] or "待填",
            }
            for item in records
        ],
    }


def _fn_schema(name: str, description: str, properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name, "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required or []},
        },
    }


ACTIVITY_FIELDS = {
    "activity_id": {"type": "string", "description": "活動 ID；已知時優先使用"},
    "activity_name": {"type": "string", "description": "活動名稱；不知道 ID 時使用"},
}
CREATE_SCHEMA = _fn_schema(
    "create_activity", "建立或沿用一場活動的正式資料，後續可接續分工、待辦、企劃書與網宣。",
    {
        "name": {"type": "string", "description": "活動名稱"},
        "activity_type": {"type": "string", "description": "茶會、社課、講座、營隊等"},
        "academic_year": {"type": "string"}, "semester": {"type": "string"},
        "start_at": {"type": "string", "description": "YYYY-MM-DD 或 ISO 日期時間；未知留空"},
        "end_at": {"type": "string", "description": "YYYY-MM-DD 或 ISO 日期時間；未知留空"},
        "location": {"type": "string"}, "goal": {"type": "string"}, "audience": {"type": "string"},
        "signup_url": {"type": "string", "description": "報名網址或報名方式；未知留空"},
        "owner": {"type": "string"}, "status": {"type": "string"}, "notes": {"type": "string"},
        "tasks": {
            "type": "array", "description": "可同時建立的初始分工與待辦",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"}, "group_name": {"type": "string"},
                    "assignee": {"type": "string"}, "due_at": {"type": "string"},
                    "status": {"type": "string"}, "priority": {"type": "string"}, "notes": {"type": "string"},
                },
                "required": ["title"],
            },
        },
    },
    ["name"],
)
UPDATE_SCHEMA = _fn_schema(
    "update_activity", "修改既有活動的日期、地點、目標、負責人、報名方式或狀態。",
    {
        **ACTIVITY_FIELDS, "name": {"type": "string"}, "activity_type": {"type": "string"},
        "academic_year": {"type": "string"}, "semester": {"type": "string"},
        "start_at": {"type": "string"}, "end_at": {"type": "string"}, "location": {"type": "string"},
        "goal": {"type": "string"}, "audience": {"type": "string"}, "signup_url": {"type": "string"},
        "owner": {"type": "string"}, "status": {"type": "string"}, "notes": {"type": "string"},
    },
)
ADD_TASK_SCHEMA = _fn_schema(
    "add_activity_task", "替活動新增分工或待辦，包含組別、負責人與期限。",
    {
        **ACTIVITY_FIELDS, "title": {"type": "string", "description": "工作項目"},
        "group_name": {"type": "string"}, "assignee": {"type": "string"},
        "due_at": {"type": "string", "description": "YYYY-MM-DD 或 ISO 日期時間；未知留空"},
        "status": {"type": "string"}, "priority": {"type": "string"}, "notes": {"type": "string"},
    },
    ["title"],
)
UPDATE_TASK_SCHEMA = _fn_schema(
    "update_activity_task", "更新一項活動工作的負責人、期限、狀態或內容。",
    {
        "task_id": {"type": "string"}, "title": {"type": "string"}, "group_name": {"type": "string"},
        "assignee": {"type": "string"}, "due_at": {"type": "string"}, "status": {"type": "string"},
        "priority": {"type": "string"}, "notes": {"type": "string"},
    },
    ["task_id"],
)
STATUS_SCHEMA = _fn_schema(
    "get_activity_status", "回答活動缺什麼、誰負責什麼、哪些工作逾期、活動前還剩哪些事項；也可提供產出前待填欄位。",
    {
        **ACTIVITY_FIELDS,
        "output_type": {"type": "string", "description": "狀態、企劃書、網宣、簡報或會議紀錄"},
    },
)
LIST_SCHEMA = _fn_schema(
    "list_activities", "依狀態、學期或活動類型列出活動。",
    {
        "status": {"type": "string"}, "semester": {"type": "string"},
        "activity_type": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 100},
    },
)
