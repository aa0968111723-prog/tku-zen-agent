"""Orchestration state —— 代理對「這一輪在做什麼」的明確認知。

為什麼不只靠 messages：訊息串會被 trim，長任務跑到一半就忘記自己在做什麼。
把狀態抽出來獨立存，才能在壓縮對話之後仍然知道還有哪幾步沒做。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any


class Stage(str, Enum):
    UNDERSTAND = "understand"
    PLAN = "plan"
    RETRIEVE = "retrieve"
    EXECUTE = "execute"
    VERIFY = "verify"
    REPAIR = "repair"
    DELIVER = "deliver"
    BLOCKED = "blocked"      # 缺必要事實，等使用者回答
    FAILED = "failed"


class WorkflowStatus(str, Enum):
    """可由使用者理解、也可由 API 控制的任務生命週期。"""

    IN_PROGRESS = "in_progress"
    PAUSED = "paused"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskType(str, Enum):
    QUESTION = "question"            # 只是問問題，不用產檔
    EVENT_PLANNING = "event_planning"
    RECRUITMENT = "recruitment"
    EVALUATION = "evaluation"
    FINANCE = "finance"
    DOCUMENTS = "documents"
    HANDOVER = "handover"
    KNOWLEDGE = "knowledge"
    UNKNOWN = "unknown"
    SOCIAL_RESEARCH = "social_research"
    SOCIAL_PUBLICITY = "social_publicity"
    COMPOSITE = "composite"


@dataclass
class PlanStep:
    description: str
    done: bool = False
    note: str = ""
    step_id: str = ""
    kind: str = "task"
    status: str = "pending"       # pending | running | completed | failed | skipped
    attempts: int = 0
    last_error: str = ""
    depends_on: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        # 舊版 state 只有 done 欄位，讀回時自動補上新狀態。
        if not self.step_id:
            digest = hashlib.sha1(self.description.encode("utf-8"), usedforsecurity=False).hexdigest()[:10]
            self.step_id = f"step-{digest}"
        if self.done:
            self.status = "completed"
        elif self.status == "completed":
            self.done = True


@dataclass
class OrchestrationState:
    """一輪任務的完整狀態。可序列化，存進 working_memory 後能還原。"""

    intent: str = ""
    task_type: TaskType = TaskType.UNKNOWN
    stage: Stage = Stage.UNDERSTAND

    required_facts: list[str] = field(default_factory=list)
    missing_facts: list[str] = field(default_factory=list)
    known_facts: dict[str, str] = field(default_factory=dict)

    plan_steps: list[PlanStep] = field(default_factory=list)
    retrieval_queries: list[str] = field(default_factory=list)
    retrieved_sources: list[str] = field(default_factory=list)

    selected_skill: str = ""
    artifacts_expected: list[str] = field(default_factory=list)
    artifacts_produced: list[dict[str, Any]] = field(default_factory=list)

    verification_rules: list[str] = field(default_factory=list)
    verification_results: list[dict[str, Any]] = field(default_factory=list)
    repair_attempts: int = 0

    completion_status: str = "in_progress"   # in_progress | completed | blocked | failed
    tool_rounds: int = 0
    workflow_status: WorkflowStatus = WorkflowStatus.IN_PROGRESS
    current_step_id: str = ""
    last_error_code: str = ""
    next_action: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)

    # ── 進度 ─────────────────────────────────────────────

    def mark_step(self, index: int, note: str = "") -> None:
        if 0 <= index < len(self.plan_steps):
            step = self.plan_steps[index]
            step.done = True
            step.status = "completed"
            if note:
                step.note = note

    def start_step(self, index: int) -> None:
        if 0 <= index < len(self.plan_steps):
            step = self.plan_steps[index]
            if step.status != "completed":
                step.status = "running"
                step.attempts += 1
                self.current_step_id = step.step_id

    def fail_step(self, index: int, error: str, *, code: str = "") -> None:
        if 0 <= index < len(self.plan_steps):
            step = self.plan_steps[index]
            step.done = False
            step.status = "failed"
            step.last_error = error[:500]
            self.last_error_code = code
            self.current_step_id = step.step_id

    def reset_failed_steps(self) -> int:
        """只把失敗步驟恢復成待執行，保留已完成步驟。"""
        count = 0
        for step in self.plan_steps:
            if step.status == "failed":
                step.status = "pending"
                step.done = False
                step.last_error = ""
                count += 1
        if count:
            self.workflow_status = WorkflowStatus.IN_PROGRESS
            self.completion_status = "in_progress"
            self.stage = Stage.EXECUTE
            self.last_error_code = ""
        return count

    def next_step(self) -> PlanStep | None:
        return next((s for s in self.plan_steps if not s.done and s.status != "skipped"), None)

    def progress(self) -> tuple[int, int]:
        return sum(1 for s in self.plan_steps if s.done or s.status == "completed"), len(self.plan_steps)

    def needs_artifacts(self) -> bool:
        return bool(self.artifacts_expected)

    # ── 序列化 ────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["task_type"] = self.task_type.value
        d["stage"] = self.stage.value
        d["workflow_status"] = self.workflow_status.value
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "OrchestrationState":
        data = dict(data)
        steps = [PlanStep(**s) if isinstance(s, dict) else s for s in data.pop("plan_steps", [])]
        try:
            task_type = TaskType(data.pop("task_type", "unknown"))
        except ValueError:
            task_type = TaskType.UNKNOWN
        try:
            stage = Stage(data.pop("stage", "understand"))
        except ValueError:
            stage = Stage.UNDERSTAND
        try:
            workflow_status = WorkflowStatus(
                data.pop("workflow_status", data.get("completion_status", "in_progress"))
            )
        except ValueError:
            workflow_status = WorkflowStatus.IN_PROGRESS
        allowed = {
            f for f in cls.__dataclass_fields__
            if f not in {"plan_steps", "task_type", "stage", "workflow_status"}
        }
        clean = {k: v for k, v in data.items() if k in allowed}
        return cls(plan_steps=steps, task_type=task_type, stage=stage, workflow_status=workflow_status, **clean)

    # ── 給 UI 的摘要（不是 chain-of-thought）──────────────

    def public_summary(self) -> dict[str, Any]:
        done, total = self.progress()
        return {
            "task_type": self.task_type.value,
            "skill": self.selected_skill,
            "stage": self.stage.value,
            "workflow_status": self.workflow_status.value,
            "steps_done": done,
            "steps_total": total,
            "status": self.completion_status,
            "current_step": self.next_step().description if self.next_step() else "",
            "next_action": self.next_action or (self.next_step().description if self.next_step() else ""),
            "failed_steps": [
                {"step_id": s.step_id, "description": s.description, "error": s.last_error}
                for s in self.plan_steps if s.status == "failed"
            ],
            "metrics": dict(self.metrics),
        }
