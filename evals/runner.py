"""Eval runner —— 九個評分維度。

  intent accuracy      任務類型分對了嗎
  skill routing        路由到對的 skill 了嗎
  tool selection       用了對的工具嗎、有沒有暴露多餘的工具
  retrieval relevance  檢索結果切題嗎
  grounding            回覆有沒有依據檢索內容
  hallucination        有沒有編造今年的事實（門檻：0）
  task completion      該產的檔產出來了嗎
  artifact validity    產出通過驗證了嗎
  latency              一輪多久
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from app import orchestrator
from app.services import current_term as term_service
from app.services.context import RequestContext
from app.services.session_store import SessionStore

from .cases import CASES, Case
from .scripted import ScriptedModel

# 沒設定當期資料時，回覆裡出現這些就是編造
FABRICATION_MARKERS = {
    "president": ("社長是", "社長：", "社長為"),
    "regular_meeting_time": ("每週一", "每週二", "每週三", "每週四", "每週五", "星期一", "星期二", "星期三"),
    "regular_meeting_location": ("H1", "SG4", "E3", "教室是"),
    "club_fee": ("元", "塊"),
    "signup_url": ("http", "forms.gle", "reurl"),
}

HONEST_MARKERS = ("還沒設定", "沒有設定", "不知道", "請提供", "待填", "請到", "補上")


@dataclass
class CaseResult:
    case_id: str
    message: str
    scores: dict[str, float] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    latency_ms: float = 0.0
    events: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        # 幻覺是硬門檻，其餘取平均
        if self.scores.get("hallucination", 1.0) < 1.0:
            return False
        core = [v for k, v in self.scores.items() if k != "latency"]
        return bool(core) and sum(core) / len(core) >= 0.75


@dataclass
class Summary:
    results: list[CaseResult] = field(default_factory=list)

    def dimension(self, name: str) -> float:
        vals = [r.scores[name] for r in self.results if name in r.scores]
        return sum(vals) / len(vals) if vals else 0.0

    def pass_rate(self) -> float:
        return sum(1 for r in self.results if r.passed) / len(self.results) if self.results else 0.0

    def hallucinations(self) -> list[CaseResult]:
        return [r for r in self.results if r.scores.get("hallucination", 1.0) < 1.0]

    def avg_latency_ms(self) -> float:
        vals = [r.latency_ms for r in self.results]
        return sum(vals) / len(vals) if vals else 0.0

    def report(self) -> str:
        lines = [
            "═" * 68,
            "  淡江大學領袖禪學社 AI 代理 —— Agent Evals",
            "═" * 68,
            "",
            f"  情境數：{len(self.results)}　通過率：{self.pass_rate()*100:.0f}%",
            f"  平均延遲：{self.avg_latency_ms():.0f} ms",
            "",
            "  ── 評分維度 ──",
        ]
        for dim in (
            "intent", "skill_routing", "tool_selection", "retrieval_relevance",
            "grounding", "hallucination", "task_completion", "artifact_validity",
        ):
            score = self.dimension(dim)
            bar = "█" * int(score * 20) + "░" * (20 - int(score * 20))
            flag = "" if score >= 0.9 else ("  ← 注意" if score >= 0.7 else "  ← 不合格")
            lines.append(f"    {dim:<22} {bar} {score*100:5.1f}%{flag}")

        bad = self.hallucinations()
        lines += ["", f"  ── 幻覺（門檻必須為 0）：{len(bad)} 件 ──"]
        for r in bad:
            lines.append(f"    ✗ {r.case_id}：{r.message}")
            lines += [f"        {n}" for n in r.notes if "編造" in n]

        failed = [r for r in self.results if not r.passed]
        if failed:
            lines += ["", f"  ── 未通過 {len(failed)} 件 ──"]
            for r in failed:
                weak = ", ".join(f"{k}={v:.1f}" for k, v in sorted(r.scores.items()) if v < 1.0 and k != "latency")
                lines.append(f"    · {r.case_id:<10} {r.message[:26]:<28} {weak}")
                lines += [f"        {n}" for n in r.notes[:3]]

        lines += ["", "═" * 68]
        return "\n".join(lines)


async def run_case(case: Case, store: SessionStore, *, live: bool = False) -> CaseResult:
    from app.orchestrator import planner

    result = CaseResult(case_id=case.id, message=case.message)

    # 每個案例一份乾淨的當期狀態
    term_service.invalidate()
    if case.term_fields:
        term_service.update(case.term_fields)

    uid = store.ensure_user(f"u_eval_{case.id}", is_local=True)
    ctx = RequestContext(user_id=uid, session_id=store.create_session(uid))

    # 1. intent + skill routing（不需要模型）
    state, routing = planner.understand(case.message)
    result.scores["skill_routing"] = 1.0 if routing.skill.name == case.skill else 0.0
    if routing.skill.name != case.skill:
        result.notes.append(f"路由到 {routing.skill.name}，期望 {case.skill}")

    want_artifact = bool(case.artifacts)
    result.scores["intent"] = 1.0 if bool(state.artifacts_expected) == want_artifact else 0.0
    if bool(state.artifacts_expected) != want_artifact:
        result.notes.append(
            f"產出意圖判斷錯誤：預期{'要' if want_artifact else '不'}產檔，實際 {state.artifacts_expected}"
        )

    # 2. 檢索相關度
    from app import retrieval

    bundle = retrieval.build_context(state.retrieval_queries)
    blob = " ".join(h.chunk.source + " " + h.chunk.text[:300] for h in bundle.hits)
    if case.expect_context:
        hit = sum(1 for kw in case.expect_context if kw in blob)
        result.scores["retrieval_relevance"] = hit / len(case.expect_context)
        if hit < len(case.expect_context):
            missing = [kw for kw in case.expect_context if kw not in blob]
            result.notes.append(f"檢索沒帶到：{missing}")
    else:
        result.scores["retrieval_relevance"] = 1.0 if bundle.hits else 0.0

    # 3. 跑完整輪
    model = ScriptedModel(case)
    events: list[dict[str, Any]] = []
    started = time.perf_counter()

    if live:
        from app.llm import NvidiaClient

        client_factory = lambda **_: NvidiaClient()  # noqa: E731
    else:
        client_factory = lambda **_: model  # noqa: E731

    import app.orchestrator as orch_mod

    original = orch_mod.NvidiaClient
    orch_mod.NvidiaClient = client_factory  # type: ignore[assignment]
    try:
        async for ev in orchestrator.run_turn(ctx, case.message, destination="local"):
            events.append(ev)
    finally:
        orch_mod.NvidiaClient = original  # type: ignore[assignment]

    result.latency_ms = (time.perf_counter() - started) * 1000
    result.scores["latency"] = 1.0 if result.latency_ms < 5000 else 0.5
    result.events = [e["type"] for e in events]

    # 4. 工具選擇
    used = [e["name"] for e in events if e["type"] == "tool_completed"]
    if case.tools:
        got = sum(1 for t in case.tools if t in used)
        result.scores["tool_selection"] = got / len(case.tools)
        if got < len(case.tools):
            result.notes.append(f"沒用到期望的工具 {[t for t in case.tools if t not in used]}，實際用了 {used}")
    else:
        result.scores["tool_selection"] = 1.0

    # 5. 任務完成
    ready = [e for e in events if e["type"] == "artifact_ready"]
    if want_artifact:
        result.scores["task_completion"] = 1.0 if ready else 0.0
        if not ready:
            result.notes.append("期望產檔但沒有 artifact_ready")
    else:
        result.scores["task_completion"] = 0.0 if ready else 1.0
        if ready:
            result.notes.append("純查詢卻產了檔")

    # 6. 產出有效性
    if ready:
        ok = sum(1 for e in ready if e.get("verified"))
        result.scores["artifact_validity"] = ok / len(ready)
        if ok < len(ready):
            result.notes.append("有產出沒通過驗證")
    elif not want_artifact:
        result.scores["artifact_validity"] = 1.0

    # 7. grounding：有檢索到東西就該用上
    final = " ".join(e.get("text", "") for e in events if e["type"] == "message")
    if bundle.hits and not want_artifact:
        grounded = any(k in final for k in ("依據", "知識庫", "劇本", "歷年")) or any(
            kw in final for kw in case.expect_context
        )
        honest = any(h in final for h in HONEST_MARKERS)
        result.scores["grounding"] = 1.0 if (grounded or honest) else 0.0
        if not (grounded or honest):
            result.notes.append("回覆沒有引用檢索內容也沒有說不知道")
    else:
        result.scores["grounding"] = 1.0

    # 8. 幻覺：當期資料沒設定卻講出具體值
    result.scores["hallucination"] = 1.0
    term = term_service.load()
    for key in case.must_not_fabricate:
        if term.get(key):
            continue
        markers = FABRICATION_MARKERS.get(key, ())
        said = [m for m in markers if m in final]
        honest = any(h in final for h in HONEST_MARKERS)
        if said and not honest:
            result.scores["hallucination"] = 0.0
            result.notes.append(f"編造了未設定的今年事實 {key}：{said}")

    # 產出檔案裡也不可以編
    for e in ready:
        if e.get("verified") is False:
            result.scores["hallucination"] = min(result.scores["hallucination"], 0.0)
            result.notes.append("產出未通過驗證卻仍交付")

    return result


async def run_all(cases: list[Case] | None = None, *, live: bool = False) -> Summary:
    import tempfile
    from pathlib import Path

    from app import config

    tmp = Path(tempfile.mkdtemp(prefix="tku_eval_"))
    original_db, original_out, original_term = config.DB_PATH, config.OUTPUT_DIR, config.CURRENT_TERM_FILE
    config.DB_PATH = tmp / "eval.sqlite3"
    config.OUTPUT_DIR = tmp / "outputs"
    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    config.CURRENT_TERM_FILE = tmp / "current_term.yaml"

    from app.services import session_store as ss

    original_store = ss._store
    store = SessionStore(config.DB_PATH)
    ss._store = store

    summary = Summary()
    try:
        for case in cases or CASES:
            summary.results.append(await run_case(case, store, live=live))
    finally:
        store.close()
        ss._store = original_store
        config.DB_PATH, config.OUTPUT_DIR, config.CURRENT_TERM_FILE = original_db, original_out, original_term
        term_service.invalidate()
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)
    return summary


def main(live: bool = False) -> int:
    summary = asyncio.run(run_all(live=live))
    print(summary.report())
    return 0 if not summary.hallucinations() and summary.pass_rate() >= 0.9 else 1
