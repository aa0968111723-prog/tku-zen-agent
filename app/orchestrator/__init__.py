"""Agent Orchestrator：Understand → Plan → Retrieve → Execute → Verify → Repair → Deliver。

跟原本的 LLM→tool→LLM 迴圈差在哪：

  · 檢索改成**先做**。原本要等模型自己想到呼叫 search_knowledge，開源模型
    常常跳過就直接開始編。現在規則層先把相關內容撈好放進 system prompt，
    模型仍然可以再查，但預設就是有依據的。

  · 驗證改成**固定階段**，不是暴露一個工具期待模型自己想到要驗。

  · 狀態獨立於 messages。對話壓縮不會讓長任務忘記自己做到哪一步。

  · 對 UI 只送結構化進度事件，不送推理過程。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Any, AsyncIterator

from .. import config, retrieval, tools, verification
from ..llm import LLMError, NvidiaClient, Reply, estimate_cost, select_model
from ..research import entities as research_entities
from ..research import verifier as research_verifier
from ..research.claims import SourceRecord
from ..research.entities import ResearchMode, ResearchScope
from ..services import activities as activity_service
from ..services import context as ctx_mod
from ..services import current_term as term_service
from ..services import fal as fal_service
from ..services import memory as memory_service
from ..services.session_store import get_store
from ..skills import SKILL_BY_NAME
from . import planner
from .prompt import build_system_prompt
from .state import OrchestrationState, Stage, WorkflowStatus

MAX_TOOL_ROUNDS = 8
MAX_REPAIRS = 2
logger = logging.getLogger(__name__)

# 產檔類工具 —— 產出後要進 verify
ARTIFACT_TOOLS = {
    "create_spreadsheet", "create_document", "create_slides", "create_google_form",
    "create_social_post", "create_social_carousel", "create_social_story", "create_reels_script",
    "create_social_content_calendar", "create_social_ab_test", "create_social_image_prompt",
    "create_social_video_prompt",
}
ACTIVITY_TOOLS = {
    "create_activity", "update_activity", "add_activity_task", "update_activity_task",
    "get_activity_status", "list_activities",
}
READ_ONLY_CACHEABLE_TOOLS = {
    "search_knowledge", "search_previous_examples", "get_current_term",
    "search_social_references", "compare_social_strategies", "analyze_social_positioning",
    "get_activity_status", "list_activities", "read_artifact",
}

# 外校研究工具 —— 回傳的 references 會登記成本輪的來源紀錄
RESEARCH_TOOLS = {
    "search_social_references", "compare_social_strategies", "analyze_social_positioning",
}

# 只有淡江內部資料的檢索工具 —— 外部研究模式下不得使用（不能拿淡江資料當外校證據）。
# get_current_term 也在列：淡江本學期的社長／社課時間一旦在外校研究回合流進
# context，「北醫現在的社長是誰」就可能被答成淡江社長（grok 審查抓到的通道）。
INTERNAL_RETRIEVAL_TOOLS = {"search_knowledge", "search_previous_examples", "get_current_term"}


def _scope_tool_guard(scope: ResearchScope, name: str) -> dict[str, Any] | None:
    """研究範圍與工具的硬邊界。

    工具 schema 已經按範圍縮限，但 dispatch 會執行任何註冊過的工具，
    模型手滑呼叫沒暴露的工具時，這裡是最後一道閘門（稽核殘餘路徑 B）。
    """
    if scope.mode == ResearchMode.EXTERNAL and name in INTERNAL_RETRIEVAL_TOOLS:
        return {
            "ok": False,
            "code": "scope_blocked",
            "message": (
                "目前是外校研究模式。知識庫、歷年檔案與本學期資料都只屬於"
                "淡江大學領袖禪學社，不能作為研究對象的證據，也不得寫成研究對象的"
                "社長、時間或活動。請改用外校研究工具查公開參考資料；"
                "查不到就誠實說找不到可靠來源。"
            ),
        }
    if scope.mode == ResearchMode.INTERNAL and name in RESEARCH_TOOLS:
        return {
            "ok": False,
            "code": "scope_blocked",
            "message": (
                "目前是淡江內部模式，不查外部參考資料。"
                "要研究其他學校，請明確說出研究對象（學校與正式社團或公開帳號）。"
            ),
        }
    return None


def _preview(args: dict[str, Any]) -> str:
    for key in ("query", "filename", "form_title", "title", "keys"):
        if args.get(key):
            return str(args[key])[:60]
    return ""


def _step_index_for_tool(state: OrchestrationState, name: str) -> int | None:
    if name in {"search_social_references", "compare_social_strategies", "analyze_social_positioning"}:
        wanted = ("research", "研究")
    elif name in ACTIVITY_TOOLS:
        wanted = ("activity", "活動")
    elif name in ARTIFACT_TOOLS:
        wanted = ("artifact", "建立", "產出")
    else:
        return None
    for i, step in enumerate(state.plan_steps):
        if step.status in {"completed", "skipped"}:
            continue
        if step.kind in wanted or any(word in step.description for word in wanted[1:]):
            return i
    return None


def _verification_index(state: OrchestrationState) -> int | None:
    for i, step in enumerate(state.plan_steps):
        if step.kind == "verify" or "檢查產出" in step.description:
            return i
    return None


def _record_usage(state: OrchestrationState, reply: Reply, model_name: str) -> None:
    metrics = state.metrics
    metrics["model_calls"] = int(metrics.get("model_calls", 0)) + 1
    metrics.setdefault("estimated_cost_usd", 0.0)
    by_model = metrics.setdefault("model_calls_by_model", {})
    by_model[model_name] = int(by_model.get(model_name, 0)) + 1
    metrics["cost_basis"] = (
        "依環境設定的每百萬 token 單價估算"
        if config.NVIDIA_INPUT_COST_PER_MILLION or config.NVIDIA_OUTPUT_COST_PER_MILLION
        else "NVIDIA Build 額度模式；美元單價未設定，成本顯示 0"
    )
    usage = reply.raw.get("usage") if isinstance(reply.raw, dict) else None
    if isinstance(usage, dict):
        for key, target in (("prompt_tokens", "input_tokens"), ("completion_tokens", "output_tokens"), ("total_tokens", "total_tokens")):
            if usage.get(key) is not None:
                metrics[target] = int(metrics.get(target, 0)) + int(usage[key])
        metrics["estimated_cost_usd"] = estimate_cost(
            int(metrics.get("input_tokens", 0)), int(metrics.get("output_tokens", 0)),
        )


def _tool_cache_key(name: str, arguments: dict[str, Any]) -> str:
    return name + ":" + json.dumps(arguments, ensure_ascii=False, sort_keys=True, default=str)


def _tool_result_for_model(result: dict[str, Any]) -> dict[str, Any]:
    payload = {"ok": result.get("ok", True), "message": result.get("message", "")}
    for key in ("activity_brief", "readiness", "activities", "task", "artifact", "content", "truncated", "alternative", "code"):
        if result.get(key) is not None:
            payload[key] = result[key]
    return payload


def _tool_alternative(name: str, code: str) -> str:
    if code in {"activity_not_found", "activity_task_not_found"}:
        return "可先建立或列出活動，再接續原本操作；需要先出草稿時，未知欄位一律標示待填。"
    if code == "unknown_tool":
        return "目前沒有連上這項能力；可改用已連線的知識庫、活動資料或文件工具完成可行部分。"
    if code == "tool_timeout":
        return "已保存其他步驟，可稍後只重試這一步，或縮小查詢範圍。"
    return "可重試失敗步驟；若仍失敗，先保留已完成內容並改用人工補充資料。"


def _control_state(store, project_id: str) -> OrchestrationState | None:
    latest = memory_service.load_state(store, project_id)
    if latest and latest.workflow_status in {WorkflowStatus.PAUSED, WorkflowStatus.CANCELLED}:
        return latest
    return None


def _failure_step_index(state: OrchestrationState) -> int | None:
    """模型失敗也必須落到可重試的步驟，不能只改整體狀態。"""
    if state.current_step_id:
        for index, step in enumerate(state.plan_steps):
            if step.step_id == state.current_step_id and step.status not in {"completed", "skipped"}:
                return index
    for index, step in enumerate(state.plan_steps):
        if step.status not in {"completed", "skipped"}:
            return index
    return len(state.plan_steps) - 1 if state.plan_steps else None


def _merge_control_status(state: OrchestrationState, controlled: OrchestrationState) -> None:
    state.workflow_status = controlled.workflow_status
    state.completion_status = controlled.completion_status
    state.next_action = controlled.next_action
    if controlled.workflow_status == WorkflowStatus.CANCELLED:
        state.stage = Stage.FAILED


def _control_command(message: str) -> str:
    text = (message or "").strip()
    if text in {"暫停", "暫停任務", "先暫停這個任務"}:
        return "pause"
    if text in {"繼續", "繼續任務", "恢復任務", "接續剛才"}:
        return "resume"
    if text in {"重試", "重試失敗步驟", "重新執行失敗步驟"}:
        return "retry"
    if text in {"取消", "取消任務", "取消這個任務"}:
        return "cancel"
    return ""


async def run_turn(
    ctx: ctx_mod.RequestContext,
    user_message: str,
    *,
    destination: str,
    model: str | None = None,
    attachments: list[dict[str, str]] | None = None,
    requested: dict[str, Any] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """跑完一輪。事件會即時 yield 出去給前端。

    ``requested``：前端傳來的結構化研究欄位（mode / school / entity_id）。
    """
    with ctx_mod.use(ctx):
        async for event in _run(
            ctx, user_message, destination=destination, model=model,
            attachments=attachments, requested=requested,
        ):
            yield event


async def _run(
    ctx: ctx_mod.RequestContext,
    user_message: str,
    *,
    destination: str,
    model: str | None,
    attachments: list[dict[str, str]] | None,
    requested: dict[str, Any] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    store = get_store()
    session_id = ctx.session_id or ""

    # ── Understand ───────────────────────────────────────
    session = store.get_session(session_id, ctx.user_id) if session_id else None
    existing_project_id = ctx.project_id or (session or {}).get("project_id")
    previous = memory_service.load_state(store, existing_project_id) if existing_project_id else None
    command = _control_command(user_message)
    restart = bool(previous and re.search(r"取消.*重新執行|重新執行(?:這個|上一個)?任務", user_message))
    if command and existing_project_id and previous and not (
        command == "resume" and previous.workflow_status == WorkflowStatus.COMPLETED
    ):
        if command == "pause":
            previous.workflow_status = WorkflowStatus.PAUSED
            previous.next_action = "說「繼續」或按繼續恢復"
            memory_service.save_state(store, existing_project_id, previous)
            yield {"type": "task_paused", "summary": previous.public_summary()}
            return
        if command == "resume":
            previous.workflow_status = WorkflowStatus.IN_PROGRESS
            previous.completion_status = "in_progress"
            previous.next_action = previous.next_step().description if previous.next_step() else ""
            memory_service.save_state(store, existing_project_id, previous)
            yield {"type": "task_resumed", "summary": previous.public_summary()}
            return
        if command == "retry":
            count = previous.reset_failed_steps()
            if count:
                previous.next_action = "只重試失敗步驟；請說「接續剛才」開始"
            memory_service.save_state(store, existing_project_id, previous)
            yield {"type": "task_retry_ready", "reset_steps": count, "summary": previous.public_summary()}
            return
        if command == "cancel":
            previous.workflow_status = WorkflowStatus.CANCELLED
            previous.completion_status = "failed"
            previous.stage = Stage.FAILED
            previous.next_action = "如要再做，請說「重新執行這個任務」"
            memory_service.save_state(store, existing_project_id, previous)
            yield {"type": "task_cancelled", "summary": previous.public_summary()}
            return
    if restart and previous:
        previous.workflow_status = WorkflowStatus.IN_PROGRESS
        previous.completion_status = "in_progress"
        previous.next_action = "重新執行既有任務"
        memory_service.save_state(store, existing_project_id, previous)
        state, routing = planner.continue_previous(user_message, previous)
    elif previous and planner.is_continuation_only(user_message):
        state, routing = planner.continue_previous(user_message, previous)
    else:
        state, routing = planner.understand(user_message, previous=previous, requested=requested)
        if previous and routing.reuse_previous:
            state = memory_service.reconcile_progress(state, previous)
    project_id = memory_service.ensure_project(store, ctx, state)
    ctx = ctx_mod.RequestContext(user_id=ctx.user_id, session_id=session_id, project_id=project_id)
    ctx_mod.replace_current(ctx)
    state.workflow_status = WorkflowStatus.IN_PROGRESS
    state.completion_status = "in_progress"
    state.metrics.setdefault("tool_calls", 0)
    state.metrics.setdefault("failure_count", 0)
    state.metrics.setdefault("retry_count", 0)
    state.metrics.setdefault("estimated_cost_usd", 0.0)
    decision = select_model(
        message=user_message,
        task_type=state.task_type.value,
        needs_artifact=state.needs_artifacts(),
        composite=bool(routing.task_sequence),
        explicit=model,
    )
    state.metrics["selected_model"] = decision["model"]
    state.metrics["model_tier"] = decision["tier"]
    state.metrics["model_reason"] = decision["reason"]
    memory_service.save_state(store, project_id, state)

    scope = ResearchScope.from_dict(state.research_scope) if state.research_scope else ResearchScope()
    resolution = research_entities.resolve(
        user_message,
        awaiting_clarification=bool(previous is not None and previous.completion_status == "needs_clarification"),
    )
    # 讓工具層（search_knowledge 等）知道本輪研究範圍：
    # 外部研究模式下，淡江內部資料不得作為外校證據。
    ctx_mod.set_research_scope(scope.to_dict())

    yield {
        "type": "task_understood",
        "task_type": state.task_type.value,
        "skill": routing.label,
        "produces": [planner.ARTIFACT_LABEL.get(a, a) for a in state.artifacts_expected],
        "research_mode": state.research_mode,
        "target_schools": state.target_schools,
        "task_sequence": list(routing.task_sequence),
        "workflow_status": state.workflow_status.value,
        "model_tier": decision["tier"],
    }

    # ── 對象不明，不要猜：先反問，不做任何檢索與生成 ──────
    if state.clarification_pending:
        clarification = resolution.clarification()
        if clarification is None and state.target_schools:
            # 代名詞 follow-up（「那他們的茶會呢」）：這句話本身解析不出對象，
            # 反問卡要從繼承的研究範圍生出來，不能因為解析不到就靜默放行。
            clarification = research_entities.clarification_for_school(state.target_schools[0])
        if clarification is not None:
            state.completion_status = "needs_clarification"
            state.research_status = research_verifier.RESEARCH_NEEDS_USER
            state.workflow_status = WorkflowStatus.BLOCKED
            state.next_action = "回覆研究對象（正式名稱、IG、FB 或網址）後繼續"
            yield clarification.to_event()
            yield {
                "type": "research_status",
                "status": research_verifier.RESEARCH_NEEDS_USER,
                "label": research_verifier.RESEARCH_STATUS_LABELS[research_verifier.RESEARCH_NEEDS_USER],
            }
            text = clarification.question
            yield {"type": "message", "text": text}
            memory_service.persist_user_message(store, session_id, user_message)
            memory_service.persist(store, session_id, {"role": "assistant", "content": text})
            memory_service.save_state(store, project_id, state)
            return

    # 圖片只交由 fal.ai 視覺服務整理，文字代理不再直接收到圖片 Base64。
    # 摘要只停留在這次的模型訊息中，不能寫進任務歷史或資料庫。
    visual_summary = ""
    if attachments:
        yield {"type": "visual_analysis_started", "count": len(attachments)}
        try:
            visual_summary = await fal_service.describe_images(attachments)
        except fal_service.FalError as exc:
            state.metrics["failure_count"] = int(state.metrics.get("failure_count", 0)) + 1
            state.stage = Stage.FAILED
            state.completion_status = "failed"
            state.workflow_status = WorkflowStatus.FAILED
            state.last_error_code = exc.code
            state.next_action = "請改用文字描述圖片內容，或稍後重新加入圖片"
            memory_service.save_state(store, project_id, state)
            yield {"type": "error", "text": str(exc), "error_code": exc.code}
            return
        state.metrics["visual_attachments"] = len(attachments)
        yield {"type": "visual_analysis_completed", "count": len(attachments)}

    # ── Plan ─────────────────────────────────────────────
    state.stage = Stage.PLAN
    yield {
        "type": "plan_created",
        "steps": [s.description for s in state.plan_steps],
        "step_details": [
            {"step_id": s.step_id, "description": s.description, "status": s.status, "depends_on": s.depends_on}
            for s in state.plan_steps
        ],
        "missing_facts": [
            term_service.FIELD_BY_KEY[k].label
            for k in state.missing_facts
            if k in term_service.FIELD_BY_KEY
        ],
    }

    # ── Retrieve ─────────────────────────────────────────
    state.stage = Stage.RETRIEVE
    memory_service.save_state(store, project_id, state)
    yield {"type": "retrieval_started", "queries": state.retrieval_queries}

    research_active = scope.mode in {ResearchMode.EXTERNAL, ResearchMode.COMPARATIVE}
    scope_for_retrieval = scope if (research_active or scope.internal_only_requested) else None
    retrieval_task_type = "social_research" if routing.task_sequence else state.task_type.value
    index = await asyncio.to_thread(retrieval.get_index)
    context_fingerprint = f"{index.fingerprint}:{term_service.fingerprint()}"
    # 研究模式不吃快取：證據池要照當下的研究範圍重新過濾，不能拿別的範圍的舊 context。
    cached = None
    if scope_for_retrieval is None:
        cached = memory_service.get_cached_context(
            store, project_id, state.retrieval_queries, state.task_type.value, context_fingerprint,
            scope=scope.to_dict(),
        )
    bundle = None
    if cached:
        context_block = cached["context_text"]
        cached_meta = cached.get("meta") or {}
        state.retrieved_sources = list(cached_meta.get("source_labels") or [])
        retrieval_event = {
            "type": "retrieval_result",
            "count": int(cached_meta.get("count", 0)),
            "sources": list(cached_meta.get("display_sources") or state.retrieved_sources),
            "curated": int(cached_meta.get("curated", 0)),
            "archive": int(cached_meta.get("archive", 0)),
            "external_reference": int(cached_meta.get("external_reference", 0)),
            "conflicts": list(cached_meta.get("conflicts") or []),
            "cached": True,
        }
    else:
        bundle = await asyncio.to_thread(
            retrieval.build_context,
            state.retrieval_queries,
            task_type=retrieval_task_type,
            scope=scope_for_retrieval,
        )
        context_block = bundle.render()
        state.retrieved_sources = bundle.source_labels()
        if scope_for_retrieval is None:
            memory_service.save_cached_context(
                store, project_id, state.retrieval_queries, state.task_type.value, context_fingerprint,
                context_block,
                meta={
                    "count": len(bundle.hits),
                    "source_labels": bundle.source_labels(),
                    "display_sources": bundle.display_sources(),
                    "curated": bundle.curated_count,
                    "archive": bundle.archive_count,
                    "external_reference": getattr(bundle, "external_count", 0),
                    "conflicts": getattr(bundle, "conflicts", []),
                },
                scope=scope.to_dict(),
            )
        retrieval_event = {
            "type": "retrieval_result",
            "count": len(bundle.hits),
            "sources": bundle.display_sources(),
            "curated": bundle.curated_count,
            "archive": bundle.archive_count,
            "external_reference": getattr(bundle, "external_count", 0),
            "external_evidence": len(bundle.external_evidence_for_targets()) if research_active else 0,
            "conflicts": getattr(bundle, "conflicts", []),
            "cached": False,
        }
    if not routing.task_sequence:
        state.mark_step(0, f"{retrieval_event['count']} 段")
    memory_service.save_state(store, project_id, state)

    current_year = term_service.load().get("academic_year")
    turn_sources: list[SourceRecord] = (
        list(bundle.source_records(current_year)) if bundle is not None else []
    )

    yield retrieval_event

    # ── 沒有證據，不要下結論：外部研究但證據池是空的 ──────
    if research_active and (bundle is None or not bundle.external_evidence_for_targets()):
        async for event in _no_source_reply(
            state, scope, resolution, store, session_id, project_id, user_message,
        ):
            yield event
        return

    # ── Execute ──────────────────────────────────────────
    state.stage = Stage.EXECUTE
    memory_service.save_state(store, project_id, state)
    # 文字模型的選擇與是否加入圖片無關；圖片已在上方由 fal.ai 轉成暫時摘要。
    selected_model = model or decision["model"]
    client = _client(selected_model)
    exposed_tools = list(routing.tool_names())
    if scope.mode == ResearchMode.EXTERNAL:
        # 純外校研究：連 schema 都不給內部檢索工具——模型看不到就不會拿
        # 淡江資料當外校證據（比較模式要查淡江內部資料，所以保留）。
        exposed_tools = [n for n in exposed_tools if n not in INTERNAL_RETRIEVAL_TOOLS]
    tool_schemas = tools.schemas_for(exposed_tools)
    activity_context = activity_service.project_activity_context(store, ctx.user_id, project_id)

    messages = memory_service.build_messages(
        store,
        session_id=session_id,
        project_id=project_id,
        system_prompt=build_system_prompt(
            state=state,
            routing=routing,
            context_block=context_block,
            destination=destination,
            project_facts=memory_service.recall_facts(store, project_id),
            previous_artifacts=memory_service.recent_artifacts(store, ctx.user_id, project_id),
            research_sources=store.list_research_sources(project_id),
            activity_context=activity_context,
        ),
        user_message=(
            user_message
            if not visual_summary
            else (
                f"{user_message}\n\n"
                "【圖片可見資訊（fal 視覺服務整理，僅供本次任務使用）】\n"
                f"{visual_summary}"
            )
        ),
    )
    # 只保存使用者原始文字；圖片 Base64 與 fal 視覺摘要都不進對話歷史。
    memory_service.persist_user_message(store, session_id, user_message)

    produced: list[dict[str, Any]] = []
    repairs = 0
    final_text = ""
    tool_cache: dict[str, dict[str, Any]] = {}
    successful_tools: set[str] = set()

    while state.tool_rounds < MAX_TOOL_ROUNDS:
        controlled = _control_state(store, project_id)
        if controlled:
            state = controlled
            event_type = "task_paused" if state.workflow_status == WorkflowStatus.PAUSED else "task_cancelled"
            yield {"type": event_type, "summary": state.public_summary()}
            return
        state.tool_rounds += 1
        try:
            reply: Reply = await client.chat(messages, tool_schemas)
            controlled_after_model = _control_state(store, project_id)
            if controlled_after_model:
                event_type = (
                    "task_paused"
                    if controlled_after_model.workflow_status == WorkflowStatus.PAUSED
                    else "task_cancelled"
                )
                yield {"type": event_type, "summary": controlled_after_model.public_summary()}
                return
            _record_usage(state, reply, selected_model)
            memory_service.save_state(store, project_id, state)
        except LLMError:
            controlled_after_error = _control_state(store, project_id)
            if controlled_after_error:
                event_type = (
                    "task_paused" if controlled_after_error.workflow_status == WorkflowStatus.PAUSED else "task_cancelled"
                )
                yield {"type": event_type, "summary": controlled_after_error.public_summary()}
                return
            state.metrics["failure_count"] = int(state.metrics.get("failure_count", 0)) + 1
            state.stage = Stage.FAILED
            state.completion_status = "failed"
            state.workflow_status = WorkflowStatus.FAILED
            state.last_error_code = "model_error"
            state.next_action = "按重試失敗步驟，或稍後再試"
            failure_index = _failure_step_index(state)
            if failure_index is not None:
                state.fail_step(failure_index, "模型服務無法回應", code="model_error")
            memory_service.save_state(store, project_id, state)
            logger.exception("LLM call failed")
            yield {"type": "error", "text": "系統忙碌中，請稍後再試"}
            return
        except Exception:  # noqa: BLE001
            controlled_after_error = _control_state(store, project_id)
            if controlled_after_error:
                event_type = (
                    "task_paused" if controlled_after_error.workflow_status == WorkflowStatus.PAUSED else "task_cancelled"
                )
                yield {"type": event_type, "summary": controlled_after_error.public_summary()}
                return
            state.metrics["failure_count"] = int(state.metrics.get("failure_count", 0)) + 1
            state.stage = Stage.FAILED
            state.completion_status = "failed"
            state.workflow_status = WorkflowStatus.FAILED
            state.last_error_code = "model_unexpected_error"
            state.next_action = "按重試失敗步驟，或稍後再試"
            failure_index = _failure_step_index(state)
            if failure_index is not None:
                state.fail_step(failure_index, "模型執行發生非預期錯誤", code="model_unexpected_error")
            memory_service.save_state(store, project_id, state)
            logger.exception("model call failed")
            yield {"type": "error", "text": "系統忙碌中，請稍後再試"}
            return

        assistant_msg: dict[str, Any] = {"role": "assistant", "content": reply.content or ""}
        if reply.tool_calls:
            assistant_msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": json.dumps(tc.arguments, ensure_ascii=False)},
                }
                for tc in reply.tool_calls
            ]
        messages.append(assistant_msg)
        memory_service.persist(store, session_id, assistant_msg)

        # ── 沒有工具呼叫 = 模型認為做完了 ────────────────
        if not reply.tool_calls:
            final_text = reply.content.strip()
            break

        if reply.content.strip():
            yield {"type": "message", "text": reply.content.strip()}

        for tc in reply.tool_calls:
            step_index = _step_index_for_tool(state, tc.name)
            if step_index is not None:
                state.start_step(step_index)
                memory_service.save_state(store, project_id, state)
                yield {
                    "type": "step_started",
                    "step_id": state.plan_steps[step_index].step_id,
                    "description": state.plan_steps[step_index].description,
                }
            yield {
                "type": "tool_started",
                "name": tc.name,
                "label": tools.LABELS.get(tc.name, tc.name),
                "preview": _preview(tc.arguments),
            }

            state.metrics["tool_requests"] = int(state.metrics.get("tool_requests", 0)) + 1
            cache_key = _tool_cache_key(tc.name, tc.arguments)
            cached_tool_result = tc.name in READ_ONLY_CACHEABLE_TOOLS and cache_key in tool_cache
            attempts = 0
            scope_blocked = _scope_tool_guard(scope, tc.name)
            if scope_blocked is not None:
                result = dict(scope_blocked)
                cached_tool_result = False
            elif cached_tool_result:
                result = dict(tool_cache[cache_key])
                state.metrics["tool_cache_hits"] = int(state.metrics.get("tool_cache_hits", 0)) + 1
            else:
                while True:
                    attempts += 1
                    state.metrics["tool_calls"] = int(state.metrics.get("tool_calls", 0)) + 1
                    try:
                        result = await asyncio.wait_for(
                            asyncio.to_thread(tools.dispatch, tc.name, tc.arguments),
                            timeout=config.TOOL_TIMEOUT_SECONDS,
                        )
                    except asyncio.TimeoutError:
                        result = {
                            "ok": False, "code": "tool_timeout",
                            "message": "工具執行逾時，這一步可以單獨重試",
                        }
                    except Exception:  # noqa: BLE001
                        logger.exception("tool dispatch failed: %s", tc.name)
                        result = {
                            "ok": False, "code": "tool_failed",
                            "message": "工具執行失敗，這一步可以單獨重試",
                        }
                    retryable = (
                        tc.name in READ_ONLY_CACHEABLE_TOOLS
                        and result.get("code") == "tool_failed"
                        and attempts < config.TOOL_MAX_ATTEMPTS
                    )
                    if not retryable:
                        break
                    state.metrics["tool_retries"] = int(state.metrics.get("tool_retries", 0)) + 1
                    yield {
                        "type": "tool_retrying", "name": tc.name,
                        "label": tools.LABELS.get(tc.name, tc.name), "attempt": attempts + 1,
                        "error_code": result.get("code", "tool_failed"),
                    }
                if result.get("ok") and tc.name in READ_ONLY_CACHEABLE_TOOLS:
                    tool_cache[cache_key] = dict(result)

            controlled_after_tool = _control_state(store, project_id)
            if controlled_after_tool:
                _merge_control_status(state, controlled_after_tool)

            if not result.get("ok"):
                code = str(result.get("code") or "tool_failed")
                result.setdefault("alternative", _tool_alternative(tc.name, code))
                failures = state.metrics.setdefault("tool_failures_by_code", {})
                failures[code] = int(failures.get(code, 0)) + 1
            else:
                successful_tools.add(tc.name)

            # 外校研究工具的回傳帶有來源明細，收進本輪來源清單給閘門與來源卡用
            if tc.name in RESEARCH_TOOLS and result.get("ok") and result.get("references"):
                for ref in result["references"]:
                    record = _source_from_reference(ref)
                    if record is not None:
                        turn_sources.append(record)

            tool_msg = {
                "role": "tool",
                "tool_call_id": tc.id,
                "name": tc.name,
                "content": json.dumps(_tool_result_for_model(result), ensure_ascii=False),
            }
            messages.append(tool_msg)
            memory_service.persist(store, session_id, tool_msg)

            event: dict[str, Any] = {
                "type": "tool_completed",
                "name": tc.name,
                "label": tools.LABELS.get(tc.name, tc.name),
                "ok": bool(result.get("ok", True)),
                "detail": _tool_detail(tc.name, result),
                "cached": cached_tool_result,
                "attempts": attempts,
                "error_code": result.get("code", "") if not result.get("ok") else "",
                "alternative": result.get("alternative", ""),
            }
            yield event

            if not result.get("ok"):
                state.metrics["failure_count"] = int(state.metrics.get("failure_count", 0)) + 1
                state.metrics["retry_count"] = int(state.metrics.get("retry_count", 0)) + 1
                if step_index is not None:
                    state.fail_step(step_index, result.get("message", "工具執行失敗"), code=result.get("code", "tool_failed"))
                    state.next_action = "重試失敗步驟，或補充缺少的資料"
                    memory_service.save_state(store, project_id, state)
                    yield {
                        "type": "step_failed",
                        "step_id": state.plan_steps[step_index].step_id,
                        "error_code": result.get("code", "tool_failed"),
                        "text": result.get("message", "工具執行失敗"),
                    }
                if controlled_after_tool:
                    memory_service.save_state(store, project_id, state)
                    event_type = (
                        "task_paused" if state.workflow_status == WorkflowStatus.PAUSED else "task_cancelled"
                    )
                    yield {"type": event_type, "summary": state.public_summary()}
                    return
                continue

            if tc.name in ACTIVITY_TOOLS and step_index is not None:
                state.mark_step(step_index, result.get("message", "活動資料已更新")[:160])
                memory_service.save_state(store, project_id, state)

            if tc.name in {"search_social_references", "compare_social_strategies", "analyze_social_positioning"}:
                source_ids = memory_service.record_research_sources(store, project_id, session_id, result)
                if source_ids:
                    yield {"type": "research_sources_saved", "source_ids": source_ids, "count": len(source_ids)}
                if step_index is not None:
                    state.mark_step(step_index, f"{len(result.get('references') or [])} 個來源")
                    # 確定性研究工具已完成來源整理，下一節點可以直接使用。
                    for i, step in enumerate(state.plan_steps):
                        if step.kind == "synthesis":
                            state.mark_step(i, "研究結果已可供後續產出使用")
                            break
                    memory_service.save_state(store, project_id, state)

            # ── Verify（產檔類工具才有）──────────────────
            if tc.name in ARTIFACT_TOOLS and result.get("local_path"):
                state.stage = Stage.VERIFY
                path = Path(result["local_path"])
                yield {"type": "verification_started", "filename": result.get("filename", path.name)}

                report = await asyncio.to_thread(
                    verification.verify,
                    path,
                    task_type=state.task_type.value,
                    rules=state.verification_rules,
                    external=routing.skill.name in {"recruitment", "social_publicity"} or bool(routing.task_sequence),
                )
                state.verification_results.append(report.to_dict())

                yield {
                    "type": "verification_result",
                    "filename": report.filename,
                    "ok": report.ok,
                    "summary": report.summary(),
                    "errors": [i.message for i in report.errors],
                    "warnings": [i.message for i in report.warnings],
                }

                if report.ok:
                    art = _artifact_payload(result, report)
                    produced.append(art)
                    state.artifacts_produced.append(art)
                    memory_service.record_artifact_fact(store, project_id, art)
                    if step_index is not None:
                        state.mark_step(step_index, f"版本 {art.get('version', 1)}")
                    verify_index = _verification_index(state)
                    if verify_index is not None:
                        state.mark_step(verify_index, report.summary())
                    yield {"type": "artifact_ready", **art}
                elif repairs < MAX_REPAIRS:
                    repairs += 1
                    state.metrics["retry_count"] = int(state.metrics.get("retry_count", 0)) + 1
                    state.repair_attempts = repairs
                    state.stage = Stage.REPAIR
                    yield {
                        "type": "repair_started",
                        "filename": report.filename,
                        "attempt": repairs,
                        "reasons": [i.message for i in report.errors],
                    }
                    fix_msg = {"role": "user", "content": report.repair_instruction()}
                    messages.append(fix_msg)
                    memory_service.persist(store, session_id, fix_msg)
                else:
                    art = _artifact_payload(result, report)
                    art["warning"] = "自動檢查仍有未修正的問題，請人工確認後再使用。"
                    produced.append(art)
                    state.artifacts_produced.append(art)
                    if step_index is not None:
                        state.fail_step(step_index, "產出仍有驗證錯誤", code="verification_failed")
                    state.workflow_status = WorkflowStatus.FAILED
                    state.completion_status = "failed"
                    state.next_action = "重試失敗步驟，或先修正驗證錯誤"
                    yield {"type": "artifact_ready", **art}

            if controlled_after_tool:
                memory_service.save_state(store, project_id, state)
                event_type = "task_paused" if state.workflow_status == WorkflowStatus.PAUSED else "task_cancelled"
                yield {"type": event_type, "summary": state.public_summary()}
                return

        state.stage = Stage.EXECUTE
        memory_service.save_state(store, project_id, state)
    else:
        state.completion_status = "blocked"
        state.workflow_status = WorkflowStatus.BLOCKED
        state.next_action = "縮小需求或重試失敗步驟"
        yield {
            "type": "error",
            "text": (
                f"工具連續呼叫超過 {MAX_TOOL_ROUNDS} 輪還沒收尾，先停下來避免無限迴圈。"
                "把需求說得更具體一點再試一次，或在設定裡換一個模型。"
            ),
        }
        memory_service.save_state(store, project_id, state)
        return

    if routing.skill.name == "activity_management" and not activity_context and not (successful_tools & ACTIVITY_TOOLS):
        activity_index = _step_index_for_tool(state, "get_activity_status")
        if activity_index is not None:
            state.fail_step(activity_index, "尚未讀取或建立活動資料", code="activity_data_required")
        state.workflow_status = WorkflowStatus.BLOCKED
        state.completion_status = "blocked"
        state.next_action = "提供活動名稱，或先建立活動資料"
        memory_service.save_state(store, project_id, state)
        yield {
            "type": "task_failed", "summary": state.public_summary(),
            "text": "目前沒有可確認的活動資料。請提供活動名稱，或先建立這場活動。",
        }
        return

    if any(step.status == "failed" for step in state.plan_steps):
        state.workflow_status = WorkflowStatus.FAILED
        state.completion_status = "failed"
        state.next_action = "重試失敗步驟"
        memory_service.save_state(store, project_id, state)
        yield {
            "type": "task_failed",
            "summary": state.public_summary(),
            "text": "有一步沒有完成，已保留其他進度；可以按「重試失敗步驟」繼續。",
        }
        return

    # ── Answer Gate（送出前的 claim verification）─────────
    if not final_text:
        final_text = _fallback_summary(produced, state)

    review = await asyncio.to_thread(
        research_verifier.review_answer,
        final_text,
        scope=scope,
        resolution=resolution,
        sources=turn_sources,
    )
    state.research_status = review.research_status
    state.claim_records = [c.to_dict() for c in review.claims]

    # 一般內部任務不需要研究儀表；只有研究模式（或閘門真的抓到問題）才顯示
    research_relevant = research_active or scope.internal_only_requested or bool(review.findings)

    cards = _dedupe_cards([r.to_card() for r in turn_sources])
    # 持久化時去掉摘錄本文，控制 working_memory 的體積
    state.source_cards = [{k: v for k, v in c.items() if k != "excerpt"} for c in cards]
    if research_relevant:
        yield {"type": "source_cards", "cards": cards}
    if review.claims or review.findings:
        yield review.to_event()

    if review.verdict == "block":
        if review.contamination is not None:
            yield {
                "type": "contamination_warning",
                "message": review.contamination["message"],
                "items": review.contamination["items"],
                "actions": _contamination_actions(scope),
            }
        state.completion_status = "blocked_by_verification"
        final_text = _blocked_text(review)
    else:
        # degrade：附上「部分內容尚未驗證」等註記；allow：內部模式補資料歸屬說明
        notices = [n for n in review.notices if n not in final_text]
        if notices:
            final_text = final_text.rstrip() + "\n\n" + "\n".join(f"※ {n}" for n in notices)
        state.completion_status = "completed"

    # ── Deliver ──────────────────────────────────────────
    state.stage = Stage.DELIVER
    if review.verdict == "block":
        verify_index = _verification_index(state)
        for i in range(1, len(state.plan_steps)):
            if i != verify_index:
                state.mark_step(i)
        if verify_index is not None:
            state.fail_step(verify_index, "來源驗證未通過", code="answer_verification_failed")
        state.workflow_status = WorkflowStatus.BLOCKED
        state.next_action = "提供研究對象的官方來源，或改用淡江內部分析"
    else:
        for i in range(1, len(state.plan_steps)):
            state.mark_step(i)
        state.workflow_status = WorkflowStatus.COMPLETED
        state.next_action = "可以修改上一份產出，或沿用這個任務繼續工作"

    yield {"type": "message", "text": final_text}

    memory_service.persist(store, session_id, {"role": "assistant", "content": final_text})
    memory_service.save_state(store, project_id, state)
    memory_service.maybe_compress(store, session_id, project_id)

    done_event: dict[str, Any] = {
        "type": "task_completed",
        "summary": state.public_summary(),
        "verdict": review.verdict,
        "artifacts": [{k: v for k, v in a.items() if k != "local_path"} for a in produced],
    }
    if research_relevant:
        yield {
            "type": "research_status",
            "status": review.research_status,
            "label": research_verifier.RESEARCH_STATUS_LABELS.get(review.research_status, review.research_status),
        }
        done_event["research_status"] = review.research_status
        done_event["research_status_label"] = research_verifier.RESEARCH_STATUS_LABELS.get(
            review.research_status, review.research_status
        )
    yield done_event


# ── 研究驗證輔助 ─────────────────────────────────────────────

def _source_from_reference(ref: dict[str, Any]) -> SourceRecord | None:
    """把 social 研究工具回傳的 reference 轉成來源紀錄。"""
    if not isinstance(ref, dict) or not ref.get("excerpt"):
        return None
    return SourceRecord(
        title=str(ref.get("source") or ref.get("source_file") or ""),
        url=str(ref.get("source_url") or ""),
        publisher=str(ref.get("organization") or ref.get("school") or ""),
        captured_at=str(ref.get("captured_at") or ""),
        excerpt=str(ref.get("excerpt") or ""),
        source_type=str(ref.get("source_type") or "official_instagram"),
        entity_id=str(ref.get("entity_id") or ""),
        school=str(ref.get("school") or ""),
        organization=str(ref.get("organization") or ""),
        source_scope="external",
        authority_level=str(ref.get("authority_level") or "official"),
        is_external=True,
    ).finalize()


def _dedupe_cards(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for card in cards:
        key = card.get("source_id") or card.get("title", "")
        if key and key not in seen:
            seen.add(key)
            out.append(card)
    return out[:12]


def _contamination_actions(scope: ResearchScope) -> list[dict[str, str]]:
    """污染警示卡的動作按鈕。send_text 由前端當成新訊息送出。"""
    school = scope.target_schools[0] if scope.target_schools else "該校"
    return [
        {"label": "只重新查外校官方來源",
         "send_text": f"請只使用{school}的官方公開來源重新研究，完全不要使用淡江內部資料。"},
        {"label": "改為淡江內部分析",
         "send_text": "改為只用淡江內部資料分析這個主題就好。"},
        {"label": "取消研究", "send_text": "取消這次研究。"},
    ]


def _blocked_text(review) -> str:
    lines = [research_verifier.BLOCK_NOTICE, ""]
    reasons = [f.message for f in review.errors][:4]
    if reasons:
        lines.append("原因：")
        lines += [f"- {r}" for r in reasons]
        lines.append("")
    lines.append(
        "你可以：提供研究對象的官方 Instagram、Facebook 或網址讓我重新查證；"
        "或改用「只用淡江內部資料」的方式分析。"
    )
    return "\n".join(lines)


async def _no_source_reply(
    state: OrchestrationState,
    scope: ResearchScope,
    resolution,
    store,
    session_id: str,
    project_id: str,
    user_message: str,
):
    """外部研究但一個可驗證來源都沒有：誠實說明並收束，不呼叫模型生成。

    這是「沒有來源仍顯示研究完成」的直接修正——沒有證據池就沒有結論，
    也沒有「研究完成」。
    """
    state.research_status = research_verifier.RESEARCH_NO_SOURCE
    state.completion_status = "no_reliable_source"
    state.workflow_status = WorkflowStatus.BLOCKED
    state.next_action = "提供研究對象的官方 Instagram、Facebook 或網址，或改用淡江內部分析"

    schools = "、".join(scope.target_schools) or "指定的研究對象"
    lines = [research_verifier.NO_SOURCE_NOTICE, ""]
    lines.append(f"【研究對象】{schools}")
    lines.append("【已驗證資料】（無——目前公開資料庫裡沒有此對象的已驗證來源）")
    no_source_names = [e.name for e in resolution.no_source_entities]
    if no_source_names:
        lines.append(f"【尚待確認】{'、'.join(no_source_names)}：已知有這個社團，"
                     "但目前沒有已收錄的官方公開來源可引用。")
    else:
        lines.append(f"【尚待確認】{schools}是否有正式社團與官方帳號。")
    lines.append("")
    lines.append("要繼續研究，請提供：官方 Instagram 帳號、Facebook 專頁或官方網址，"
                 "我會只依這些可驗證來源整理。也可以改用「只用淡江內部資料」分析。")
    text = "\n".join(lines)

    yield {"type": "source_cards", "cards": []}
    yield {
        "type": "research_status",
        "status": research_verifier.RESEARCH_NO_SOURCE,
        "label": research_verifier.RESEARCH_STATUS_LABELS[research_verifier.RESEARCH_NO_SOURCE],
    }
    yield {"type": "message", "text": text}

    memory_service.persist_user_message(store, session_id, user_message)
    memory_service.persist(store, session_id, {"role": "assistant", "content": text})
    memory_service.save_state(store, project_id, state)

    yield {
        "type": "task_completed",
        "summary": state.public_summary(),
        "research_status": research_verifier.RESEARCH_NO_SOURCE,
        "research_status_label": research_verifier.RESEARCH_STATUS_LABELS[research_verifier.RESEARCH_NO_SOURCE],
        "verdict": "no_source",
        "artifacts": [],
    }


# ── 輔助 ─────────────────────────────────────────────────────

def _client(model: str | None):
    from ..llm import route_model

    return NvidiaClient(model=model or route_model("execute"))


def _tool_detail(name: str, result: dict[str, Any]) -> str:
    if name == "search_knowledge":
        n = result.get("hit_count", 0)
        return f"找到 {n} 段相關內容" if n else "知識庫沒有相關內容"
    if name == "get_current_term":
        unknown = result.get("unknown_keys") or []
        return f"{len(result.get('known_keys') or [])} 項已設定、{len(unknown)} 項未設定"
    msg = result.get("message", "")
    return msg.split("\n")[0] if msg else ""


def _artifact_payload(result: dict[str, Any], report: verification.Report) -> dict[str, Any]:
    return {
        "artifact_id": result.get("artifact_id"),
        "filename": result.get("filename"),
        "version": result.get("version", 1),
        "parent_artifact_id": result.get("parent_artifact_id"),
        "activity_id": result.get("activity_id"),
        "drive_url": result.get("drive_url"),
        "verified": report.ok,
        "warnings": [i.message for i in report.warnings],
    }


def _fallback_summary(produced: list[dict[str, Any]], state: OrchestrationState) -> str:
    if not produced:
        return "這一輪沒有產生檔案。請再說一次你要的東西，或說得更具體一點。"
    names = "、".join(a["filename"] for a in produced if a.get("filename"))
    lines = [f"做好了：{names}。"]
    if state.missing_facts:
        labels = "、".join(
            term_service.FIELD_BY_KEY[k].label
            for k in state.missing_facts
            if k in term_service.FIELD_BY_KEY
        )
        if labels:
            lines.append(f"其中 {labels} 因為「本學期設定」還沒填，我在檔案裡留了「待填」。")
    return "\n".join(lines)


def health(*, slim: bool = False) -> dict[str, Any]:
    from ..llm import MODEL_ROUTES, pool_stats

    idx = retrieval.get_index()
    if slim:
        return {
            "model": config.NVIDIA_MODEL,
            "models": config.KNOWN_TOOL_MODELS,
            "model_policy": {"fast": config.NVIDIA_FAST_MODEL, "standard": config.NVIDIA_MODEL, "strong": config.NVIDIA_STRONG_MODEL},
            "destination": config.DEFAULT_DESTINATION,
            "auth_mode": config.auth_mode(),
        }
    term = term_service.load()
    return {
        "model": config.NVIDIA_MODEL,
        "models": config.KNOWN_TOOL_MODELS,
        "model_routes": MODEL_ROUTES,
        "model_policy": {"fast": config.NVIDIA_FAST_MODEL, "standard": config.NVIDIA_MODEL, "strong": config.NVIDIA_STRONG_MODEL},
        "destination": config.DEFAULT_DESTINATION,
        "auth_mode": config.auth_mode(),
        "knowledge": idx.stats(),
        "term": {
            "label": term.term_label(),
            "configured": term.exists and bool(term.known()),
            "missing": [
                term_service.FIELD_BY_KEY[k].label
                for k in term.missing()
                if k in term_service.FIELD_BY_KEY
            ],
        },
        "skills": [{"name": s.name, "label": s.label} for s in SKILL_BY_NAME.values()],
        "tools": [{"name": n, "label": l} for n, l in tools.LABELS.items()],
        "problems": config.missing_config(),
        "drive_ready": config.GOOGLE_CREDENTIALS_FILE.exists(),
        "llm": pool_stats(),
    }
