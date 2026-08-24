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
from ..research import entities as research_entities
from ..research import verifier as research_verifier
from ..research.claims import SourceRecord
from ..research.entities import ResearchMode, ResearchScope
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

# 外校研究工具 —— 回傳的 references 會登記成本輪的來源紀錄
RESEARCH_TOOLS = {
    "search_social_references", "compare_social_strategies", "analyze_social_positioning",
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

    scope = ResearchScope.from_dict(state.research_scope) if state.research_scope else ResearchScope()
    resolution = research_entities.resolve(user_message)

    yield {
        "type": "task_understood",
        "task_type": state.task_type.value,
        "skill": routing.skill.label,
        "produces": [planner.ARTIFACT_LABEL.get(a, a) for a in state.artifacts_expected],
        "research_mode": state.research_mode,
        "target_schools": state.target_schools,
    }

    # ── 對象不明，不要猜：先反問，不做任何檢索與生成 ──────
    if state.clarification_pending:
        clarification = resolution.clarification()
        if clarification is not None:
            state.completion_status = "needs_clarification"
            state.research_status = research_verifier.RESEARCH_NEEDS_USER
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

    research_active = scope.mode in {ResearchMode.EXTERNAL, ResearchMode.COMPARATIVE}
    bundle = await asyncio.to_thread(
        retrieval.build_context,
        state.retrieval_queries,
        task_type=state.task_type.value,
        scope=scope if (research_active or scope.internal_only_requested) else None,
    )
    state.retrieved_sources = bundle.source_labels()
    state.mark_step(0, f"{len(bundle.hits)} 段")

    current_year = term_service.load().get("academic_year")
    turn_sources: list[SourceRecord] = list(bundle.source_records(current_year))

    yield {
        "type": "retrieval_result",
        "count": len(bundle.hits),
        "sources": bundle.display_sources(),
        "curated": bundle.curated_count,
        "archive": bundle.archive_count,
        "external_reference": getattr(bundle, "external_count", 0),
        "external_evidence": len(bundle.external_evidence_for_targets()) if research_active else 0,
    }

    # ── 沒有證據，不要下結論：外部研究但證據池是空的 ──────
    if research_active and not bundle.external_evidence_for_targets():
        async for event in _no_source_reply(
            state, scope, resolution, store, session_id, project_id, user_message,
        ):
            yield event
        return

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

    cards = _dedupe_cards([r.to_card() for r in turn_sources])
    # 持久化時去掉摘錄本文，控制 working_memory 的體積
    state.source_cards = [{k: v for k, v in c.items() if k != "excerpt"} for c in cards]
    if cards or research_active:
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
    for i in range(1, len(state.plan_steps)):
        state.mark_step(i)

    yield {"type": "message", "text": final_text}

    memory_service.persist(store, session_id, {"role": "assistant", "content": final_text})
    memory_service.save_state(store, project_id, state)
    memory_service.maybe_compress(store, session_id, project_id)

    yield {
        "type": "research_status",
        "status": review.research_status,
        "label": research_verifier.RESEARCH_STATUS_LABELS.get(review.research_status, review.research_status),
    }
    yield {
        "type": "task_completed",
        "summary": state.public_summary(),
        "research_status": review.research_status,
        "research_status_label": research_verifier.RESEARCH_STATUS_LABELS.get(
            review.research_status, review.research_status
        ),
        "verdict": review.verdict,
        "artifacts": [{k: v for k, v in a.items() if k != "local_path"} for a in produced],
    }


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
