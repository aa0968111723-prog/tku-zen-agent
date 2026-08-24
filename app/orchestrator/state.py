"""Orchestration state —— 代理對「這一輪在做什麼」的明確認知。

為什麼不只靠 messages：訊息串會被 trim，長任務跑到一半就忘記自己在做什麼。
把狀態抽出來獨立存，才能在壓縮對話之後仍然知道還有哪幾步沒做。
"""

from __future__ import annotations

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


@dataclass
class PlanStep:
    description: str
    done: bool = False
    note: str = ""


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

    # ── 研究驗證（政大事故後新增）────────────────────────
    research_mode: str = "internal"          # internal | external | comparative
    research_scope: dict[str, Any] = field(default_factory=dict)   # ResearchScope.to_dict()
    research_status: str = "internal"        # research.verifier 的完成狀態
    target_entities: list[str] = field(default_factory=list)
    target_schools: list[str] = field(default_factory=list)
    clarification_pending: bool = False
    source_cards: list[dict[str, Any]] = field(default_factory=list)
    claim_records: list[dict[str, Any]] = field(default_factory=list)

    # ── 進度 ─────────────────────────────────────────────

    def mark_step(self, index: int, note: str = "") -> None:
        if 0 <= index < len(self.plan_steps):
            self.plan_steps[index].done = True
            if note:
                self.plan_steps[index].note = note

    def next_step(self) -> PlanStep | None:
        return next((s for s in self.plan_steps if not s.done), None)

    def progress(self) -> tuple[int, int]:
        return sum(1 for s in self.plan_steps if s.done), len(self.plan_steps)

    def needs_artifacts(self) -> bool:
        return bool(self.artifacts_expected)

    # ── 序列化 ────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["task_type"] = self.task_type.value
        d["stage"] = self.stage.value
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
        allowed = {f for f in cls.__dataclass_fields__ if f not in {"plan_steps", "task_type", "stage"}}
        clean = {k: v for k, v in data.items() if k in allowed}
        return cls(plan_steps=steps, task_type=task_type, stage=stage, **clean)

    # ── 給 UI 的摘要（不是 chain-of-thought）──────────────

    def public_summary(self) -> dict[str, Any]:
        done, total = self.progress()
        return {
            "task_type": self.task_type.value,
            "skill": self.selected_skill,
            "stage": self.stage.value,
            "steps_done": done,
            "steps_total": total,
            "status": self.completion_status,
        }
