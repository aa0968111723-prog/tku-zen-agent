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
from ..services import activities as activity_service
from ..services import context as ctx_mod
from ..services import current_term as term_service
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
    "get_activity_status", "list_activities",
}


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
    for key in ("activity_brief", "readiness", "activities", "task", "alternative", "code"):
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
) -> AsyncIterator[dict[str, Any]]:
    """跑完一輪。事件會即時 yield 出去給前端。"""
    with ctx_mod.use(ctx):
        async for event in _run(ctx, user_message, destination=destination, model=model):
            yield event


async def _run(
    ctx: ctx_mod.RequestContext,
    user_message: str,
    *,
    destination: str,
    model: str | None,
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
        state, routing = planner.understand(user_message)
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

    yield {
        "type": "task_understood",
        "task_type": state.task_type.value,
        "skill": routing.label,
        "produces": [planner.ARTIFACT_LABEL.get(a, a) for a in state.artifacts_expected],
        "task_sequence": list(routing.task_sequence),
        "workflow_status": state.workflow_status.value,
        "model_tier": decision["tier"],
    }

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

    retrieval_task_type = "social_research" if routing.task_sequence else state.task_type.value
    index = await asyncio.to_thread(retrieval.get_index)
    cached = memory_service.get_cached_context(
        store, project_id, state.retrieval_queries, state.task_type.value, index.fingerprint
    )
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
        )
        context_block = bundle.render()
        state.retrieved_sources = bundle.source_labels()
        memory_service.save_cached_context(
            store, project_id, state.retrieval_queries, state.task_type.value, index.fingerprint,
            context_block,
            {
                "count": len(bundle.hits),
                "source_labels": bundle.source_labels(),
                "display_sources": bundle.display_sources(),
                "curated": bundle.curated_count,
                "archive": bundle.archive_count,
                "external_reference": getattr(bundle, "external_count", 0),
                "conflicts": getattr(bundle, "conflicts", []),
            },
        )
        retrieval_event = {
            "type": "retrieval_result",
            "count": len(bundle.hits),
            "sources": bundle.display_sources(),
            "curated": bundle.curated_count,
            "archive": bundle.archive_count,
            "external_reference": getattr(bundle, "external_count", 0),
            "conflicts": getattr(bundle, "conflicts", []),
            "cached": False,
        }
    if not routing.task_sequence:
        state.mark_step(0, f"{retrieval_event['count']} 段")
    memory_service.save_state(store, project_id, state)

    yield retrieval_event

    # ── Execute ──────────────────────────────────────────
    state.stage = Stage.EXECUTE
    memory_service.save_state(store, project_id, state)
    client = _client(decision["model"])
    tool_schemas = tools.schemas_for(routing.tool_names())
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
        user_message=user_message,
    )
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
            _record_usage(state, reply, decision["model"])
            memory_service.save_state(store, project_id, state)
        except LLMError:
            state.metrics["failure_count"] = int(state.metrics.get("failure_count", 0)) + 1
            state.stage = Stage.FAILED
            state.completion_status = "failed"
            state.workflow_status = WorkflowStatus.FAILED
            state.last_error_code = "model_error"
            state.next_action = "按重試失敗步驟，或稍後再試"
            state.fail_step(_step_index_for_tool(state, "model"), "模型服務無法回應", code="model_error") if _step_index_for_tool(state, "model") is not None else None
            memory_service.save_state(store, project_id, state)
            logger.exception("LLM call failed")
            yield {"type": "error", "text": "系統忙碌中，請稍後再試"}
            return
        except Exception:  # noqa: BLE001
            state.metrics["failure_count"] = int(state.metrics.get("failure_count", 0)) + 1
            state.stage = Stage.FAILED
            state.completion_status = "failed"
            state.workflow_status = WorkflowStatus.FAILED
            state.last_error_code = "model_unexpected_error"
            state.next_action = "按重試失敗步驟，或稍後再試"
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
            if cached_tool_result:
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

            if not result.get("ok"):
                code = str(result.get("code") or "tool_failed")
                result.setdefault("alternative", _tool_alternative(tc.name, code))
                failures = state.metrics.setdefault("tool_failures_by_code", {})
                failures[code] = int(failures.get(code, 0)) + 1
            else:
                successful_tools.add(tc.name)

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

    # ── Deliver ──────────────────────────────────────────
    state.stage = Stage.DELIVER
    for i in range(1, len(state.plan_steps)):
        state.mark_step(i)
    state.completion_status = "completed"
    state.workflow_status = WorkflowStatus.COMPLETED
    state.next_action = "可以修改上一份產出，或沿用這個任務繼續工作"

    if not final_text:
        final_text = _fallback_summary(produced, state)
    yield {"type": "message", "text": final_text}

    memory_service.persist(store, session_id, {"role": "assistant", "content": final_text})
    memory_service.save_state(store, project_id, state)
    memory_service.maybe_compress(store, session_id, project_id)

    yield {
        "type": "task_completed",
        "summary": state.public_summary(),
        "artifacts": [{k: v for k, v in a.items() if k != "local_path"} for a in produced],
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
