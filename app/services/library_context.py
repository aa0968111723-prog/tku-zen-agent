"""ACL-first Library → Project → Scene → Shot context resolution."""

from __future__ import annotations

import re
from typing import Any

from .session_store import SessionStore, get_store
from .visual_assets import VisualAssetStore, dumps, get_visual_store, infer_ratio, infer_school, new_id, now


_ZH_NUMBERS = {"一": 1, "二": 2, "兩": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def _position(query: str, marker: str) -> int:
    match = re.search(rf"第\s*(\d+|[一二兩三四五六七八九十])\s*{marker}", query)
    if not match:
        return 0
    raw = match.group(1)
    return int(raw) if raw.isdigit() else _ZH_NUMBERS.get(raw, 0)


class LibraryContextResolver:
    def __init__(self, session_store: SessionStore | None = None, visual_store: VisualAssetStore | None = None) -> None:
        self.session_store = session_store or get_store()
        self.visual_store = visual_store or get_visual_store()

    def upsert_node(
        self, user_id: str, *, project_id: str, node_type: str, title: str,
        position: int = 0, parent_id: str = "", requirements: dict[str, Any] | None = None,
        verification_status: str = "verified", source_id: str = "user_input",
    ) -> dict[str, Any]:
        if not self.session_store.get_project(project_id, user_id):
            raise ValueError("project 不屬於目前使用者")
        if node_type not in {"story", "scene", "shot", "output"}:
            raise ValueError("node_type 只支援 story、scene、shot、output")
        if verification_status not in {"verified", "probable", "pending_review", "conflicted", "failed"}:
            raise ValueError("verification_status 無效")
        node_id = new_id("context")
        stamp = now()
        with self.visual_store._lock:
            existing = self.visual_store._conn.execute(
                "SELECT id FROM project_context_nodes WHERE owner_id=? AND project_id=? AND node_type=? AND parent_id=? AND position=? AND title=?",
                (user_id, project_id, node_type, parent_id, int(position), title.strip()),
            ).fetchone()
            if existing:
                node_id = str(existing[0])
                self.visual_store._conn.execute(
                    "UPDATE project_context_nodes SET requirements=?,verification_status=?,source_id=?,updated_at=? WHERE id=? AND owner_id=?",
                    (dumps(requirements or {}), verification_status, source_id, stamp, node_id, user_id),
                )
            else:
                self.visual_store._conn.execute(
                    "INSERT INTO project_context_nodes(id,owner_id,project_id,parent_id,node_type,title,position,requirements,verification_status,source_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (node_id, user_id, project_id, parent_id, node_type, title.strip()[:240], int(position), dumps(requirements or {}), verification_status, source_id, stamp, stamp),
                )
            self.visual_store._conn.commit()
        return self.get_node(node_id, user_id) or {}

    def get_node(self, node_id: str, user_id: str) -> dict[str, Any] | None:
        rows = self.visual_store._rows("SELECT * FROM project_context_nodes WHERE id=? AND owner_id=?", (node_id, user_id))
        if not rows:
            return None
        rows[0]["requirements"] = self._loads(rows[0].get("requirements"))
        return rows[0]

    @staticmethod
    def _loads(value: object) -> dict[str, Any]:
        import json

        try:
            result = json.loads(str(value or "{}"))
            return result if isinstance(result, dict) else {}
        except (TypeError, ValueError):
            return {}

    def _project(self, user_id: str, query: str, project_id: str) -> dict[str, Any] | None:
        if project_id:
            project = self.session_store.get_project(project_id, user_id)
            if not project:
                raise ValueError("project 不屬於目前使用者")
            return project
        projects = self.session_store.list_projects(user_id, limit=100)
        exact = [project for project in projects if project.get("name") and str(project["name"]) in query]
        if exact:
            return max(exact, key=lambda project: len(str(project.get("name") or "")))
        if any(term in query for term in ("招生", "招新", "新生")):
            matches = [project for project in projects if project.get("task_type") == "recruitment" or "招生" in str(project.get("name") or "")]
            if matches:
                return matches[0]
        return None

    def resolve(self, user_id: str, *, query: str, project_id: str = "", scene_position: int = 0, shot_position: int = 0) -> dict[str, Any]:
        project = self._project(user_id, query, project_id)
        scene_position = int(scene_position or _position(query, "幕") or _position(query, "場"))
        shot_position = int(shot_position or _position(query, "鏡"))
        nodes: list[dict[str, Any]] = []
        if project:
            nodes = self.visual_store._rows(
                "SELECT * FROM project_context_nodes WHERE owner_id=? AND project_id=? ORDER BY CASE node_type WHEN 'story' THEN 1 WHEN 'scene' THEN 2 WHEN 'shot' THEN 3 ELSE 4 END,position",
                (user_id, project["id"]),
            )
            for node in nodes:
                node["requirements"] = self._loads(node.get("requirements"))
        scene = next((node for node in nodes if node["node_type"] == "scene" and (not scene_position or int(node["position"]) == scene_position)), None)
        shot = next((node for node in nodes if node["node_type"] == "shot" and (not shot_position or int(node["position"]) == shot_position) and (not scene or node["parent_id"] == scene["id"])), None)

        school = infer_school(query)
        ratio = infer_ratio(query)
        scene_label = ""
        requirements: dict[str, Any] = {}
        for node in (scene, shot):
            if node:
                requirements.update(node["requirements"])
                scene_label = scene_label or str(node["requirements"].get("scene") or "")
        is_recruitment = any(term in query for term in ("招生", "招新", "新生")) or (project and project.get("task_type") == "recruitment")
        if is_recruitment and scene_position == 2 and not scene:
            scene_label = "校園"
            requirements.update({"story": "進入淡江校園", "scene": "校園", "people_preference": "人物自然清楚", "brightness_min": 45, "ratio": "16:9", "quality_min": 65})
        if "教室" in query:
            scene_label = "教室"
        if "海報" in query:
            scene_label = "海報"
        if not ratio:
            ratio = str(requirements.get("ratio") or ("4:5" if "IG" in query.upper() and "直式" in query else ""))
        if not school and project and "淡江" in (str(project.get("name") or "") + str(project.get("summary") or "")):
            school = "淡江大學"
        people_min = max(int(requirements.get("people_min") or 0), 2 if any(term in query for term in ("多人", "合照")) else 0)
        quality_min = max(float(requirements.get("quality_min") or 0), 70 if any(term in query for term in ("高清", "清楚", "高畫質")) else 0)
        brightness_min = float(requirements.get("brightness_min") or (45 if "明亮" in query else 0))
        commercial_use = "allowed" if any(term in query for term in ("可商用", "可使用", "授權確認")) else ""
        context = {
            "library": {"acl": "owner_or_shared_public", "source_truth": "visual_assets"},
            "project": project and {key: project.get(key) for key in ("id", "name", "task_type", "summary")},
            "story": next((node for node in nodes if node["node_type"] == "story"), None),
            "scene": scene or ({"position": scene_position, "title": requirements.get("story") or scene_label, "requirements": requirements, "verification_status": "probable"} if scene_position else None),
            "shot": shot or ({"position": shot_position, "title": f"第 {shot_position} 鏡", "requirements": requirements, "verification_status": "probable"} if shot_position else None),
            "output_requirements": {
                "school": school, "scene": scene_label, "ratio": ratio,
                "people_min": people_min, "quality_min": quality_min,
                "brightness_min": brightness_min, "commercial_use": commercial_use,
            },
        }
        return context

    def search(self, user_id: str, *, query: str, project_id: str = "", scene_position: int = 0, shot_position: int = 0, page: int = 1, limit: int = 30) -> dict[str, Any]:
        # Ownership is resolved before any library query.  The visual store then
        # applies owner/shared/public ACL again before ranking.
        context = self.resolve(user_id, query=query, project_id=project_id, scene_position=scene_position, shot_position=shot_position)
        requirements = context["output_requirements"]
        project = context.get("project") or {}
        augmented_query = " ".join(filter(None, [query, str((context.get("scene") or {}).get("title") or ""), str(project.get("name") or "")]))
        capped = max(1, min(int(limit or 30), 60))
        items, total, parsed = self.visual_store.search(
            user_id, query=augmented_query, page=page, limit=capped,
            school=str(requirements.get("school") or ""), scene=str(requirements.get("scene") or ""),
            ratio=str(requirements.get("ratio") or ""), people_min=int(requirements.get("people_min") or 0),
            quality_min=float(requirements.get("quality_min") or 0), brightness_min=float(requirements.get("brightness_min") or 0),
            commercial_use=str(requirements.get("commercial_use") or ""), duplicate="exclude",
        )
        for item in items:
            item.setdefault("recommendation_reasons", []).insert(0, "符合 Library → Project → Scene → Shot 任務脈絡")
            item["context"] = {"project_id": project.get("id", ""), "scene_position": scene_position, "shot_position": shot_position}
        return {"context": context, "items": items, "total": total, "page": page, "limit": capped, "parsed_conditions": parsed, "acl_first": True}

