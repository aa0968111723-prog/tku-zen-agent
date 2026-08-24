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
from pathlib import Path
from typing import Any, AsyncIterator

from .. import config, retrieval, tools, verification
from ..llm import LLMError, NvidiaClient, Reply
from ..services import context as ctx_mod
from ..services import current_term as term_service
from ..services import memory as memory_service
from ..services.session_store import get_store
from ..skills import SKILL_BY_NAME
from . import planner
from .prompt import build_system_prompt
from .state import OrchestrationState, Stage

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


def _preview(args: dict[str, Any]) -> str:
    for key in ("query", "filename", "form_title", "title", "keys"):
        if args.get(key):
            return str(args[key])[:60]
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
    state, routing = planner.understand(user_message)
    project_id = memory_service.ensure_project(store, ctx, state)
    ctx = ctx_mod.RequestContext(user_id=ctx.user_id, session_id=session_id, project_id=project_id)

    yield {
        "type": "task_understood",
        "task_type": state.task_type.value,
        "skill": routing.skill.label,
        "produces": [planner.ARTIFACT_LABEL.get(a, a) for a in state.artifacts_expected],
    }

    # ── Plan ─────────────────────────────────────────────
    state.stage = Stage.PLAN
    yield {
        "type": "plan_created",
        "steps": [s.description for s in state.plan_steps],
        "missing_facts": [
            term_service.FIELD_BY_KEY[k].label
            for k in state.missing_facts
            if k in term_service.FIELD_BY_KEY
        ],
    }

    # ── Retrieve ─────────────────────────────────────────
    state.stage = Stage.RETRIEVE
    yield {"type": "retrieval_started", "queries": state.retrieval_queries}

    bundle = await asyncio.to_thread(
        retrieval.build_context,
        state.retrieval_queries,
        task_type=state.task_type.value,
    )
    state.retrieved_sources = bundle.source_labels()
    state.mark_step(0, f"{len(bundle.hits)} 段")

    yield {
        "type": "retrieval_result",
        "count": len(bundle.hits),
        "sources": bundle.display_sources(),
        "curated": bundle.curated_count,
        "archive": bundle.archive_count,
        "external_reference": getattr(bundle, "external_count", 0),
    }

    # ── Execute ──────────────────────────────────────────
    state.stage = Stage.EXECUTE
    client = _client(model)
    tool_schemas = tools.schemas_for(routing.skill.tool_names())

    messages = memory_service.build_messages(
        store,
        session_id=session_id,
        project_id=project_id,
        system_prompt=build_system_prompt(
            state=state,
            routing=routing,
            context_block=bundle.render(),
            destination=destination,
            project_facts=memory_service.recall_facts(store, project_id),
        ),
        user_message=user_message,
    )
    memory_service.persist_user_message(store, session_id, user_message)

    produced: list[dict[str, Any]] = []
    repairs = 0
    final_text = ""

    while state.tool_rounds < MAX_TOOL_ROUNDS:
        state.tool_rounds += 1
        try:
            reply: Reply = await client.chat(messages, tool_schemas)
        except LLMError as exc:
            state.stage = Stage.FAILED
            state.completion_status = "failed"
            logger.exception("LLM call failed")
            yield {"type": "error", "text": "系統忙碌中，請稍後再試"}
            return
        except Exception:  # noqa: BLE001
            state.stage = Stage.FAILED
            state.completion_status = "failed"
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
            yield {
                "type": "tool_started",
                "name": tc.name,
                "label": tools.LABELS.get(tc.name, tc.name),
                "preview": _preview(tc.arguments),
            }

            result = await asyncio.to_thread(tools.dispatch, tc.name, tc.arguments)

            tool_msg = {
                "role": "tool",
                "tool_call_id": tc.id,
                "name": tc.name,
                "content": json.dumps(
                    {"ok": result.get("ok", True), "message": result.get("message", "")},
                    ensure_ascii=False,
                ),
            }
            messages.append(tool_msg)
            memory_service.persist(store, session_id, tool_msg)

            event: dict[str, Any] = {
                "type": "tool_completed",
                "name": tc.name,
                "label": tools.LABELS.get(tc.name, tc.name),
                "ok": bool(result.get("ok", True)),
                "detail": _tool_detail(tc.name, result),
            }
            yield event

            if not result.get("ok"):
                continue

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
                    external=routing.skill.name in {"recruitment", "social_publicity"},
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
                    yield {"type": "artifact_ready", **art}
                elif repairs < MAX_REPAIRS:
                    repairs += 1
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
                    yield {"type": "artifact_ready", **art}

        state.stage = Stage.EXECUTE
    else:
        state.completion_status = "blocked"
        yield {
            "type": "error",
            "text": (
                f"工具連續呼叫超過 {MAX_TOOL_ROUNDS} 輪還沒收尾，先停下來避免無限迴圈。"
                "把需求說得更具體一點再試一次，或在設定裡換一個模型。"
            ),
        }
        memory_service.save_state(store, project_id, state)
        return

    # ── Deliver ──────────────────────────────────────────
    state.stage = Stage.DELIVER
    for i in range(1, len(state.plan_steps)):
        state.mark_step(i)
    state.completion_status = "completed"

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
            "destination": config.DEFAULT_DESTINATION,
            "auth_mode": config.auth_mode(),
        }
    term = term_service.load()
    return {
        "model": config.NVIDIA_MODEL,
        "models": config.KNOWN_TOOL_MODELS,
        "model_routes": MODEL_ROUTES,
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
