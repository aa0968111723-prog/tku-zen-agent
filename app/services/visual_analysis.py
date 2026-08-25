"""視覺資產分析流程：本機品質資料之後接 fal OCR／場景與受控人物候選。"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

from .. import config
from . import fal
from .visual_assets import VisualAssetError, VisualAssetStore, get_visual_store


def _data_url(path: str | Path, mime_type: str) -> str:
    content = Path(path).read_bytes()
    return f"data:{mime_type};base64,{base64.b64encode(content).decode('ascii')}"


def _confidence(value: Any) -> float:
    try:
        return max(0.0,min(1.0,float(value)))
    except (TypeError,ValueError):
        return 0.0


async def analyze_asset(asset_id: str, user_id: str, *, store: VisualAssetStore | None = None) -> dict[str, Any]:
    store = store or get_visual_store()
    asset = store.owned_asset(asset_id,user_id)
    if not asset:
        raise VisualAssetError("找不到可分析的圖片",code="not_found")
    job_id = store.create_job(asset_id)
    try:
        target_url = _data_url(asset["storage_path"],asset["mime_type"])
        store.update_job(job_id,status="running",stage="ocr_and_scene",progress=25)
        result = await fal.analyze_visual_asset(target_url)
        source = f"fal_vision:{config.FAL_VISION_MODEL}"
        store.set_analysis(asset_id,result,status="processing_entities")
        store.update_job(job_id,status="running",stage="entities",progress=62)

        for scene in result.get("scenes",[]) if isinstance(result.get("scenes"),list) else []:
            if not isinstance(scene,dict):
                continue
            label = str(scene.get("label") or "").strip()
            if not label:
                continue
            scene_id = store.scene_id(label)
            store.add_observation(
                asset_id,"scene",label=label,entity_id=scene_id,status="possible",
                confidence=_confidence(scene.get("confidence")),source=source,
                evidence={"text":str(scene.get("evidence") or "")[:1000]},
            )

        people = result.get("people") if isinstance(result.get("people"),dict) else {}
        descriptions = people.get("descriptions",[]) if isinstance(people,dict) else []
        for index,person in enumerate(descriptions if isinstance(descriptions,list) else []):
            if not isinstance(person,dict):
                continue
            # 此欄只描述畫面中的匿名人物，不允許模型提供姓名。
            store.add_observation(
                asset_id,"person",label=f"未辨識人物 {index + 1}",status="pending",
                confidence=_confidence(person.get("confidence")),source=source,
                evidence={"description":str(person.get("evidence") or person.get("label") or "")[:1000]},
            )

        for logo in result.get("logos",[]) if isinstance(result.get("logos"),list) else []:
            if isinstance(logo,dict) and str(logo.get("text") or "").strip():
                store.add_observation(
                    asset_id,"logo",label=str(logo["text"])[:300],status="possible",
                    confidence=_confidence(logo.get("confidence")),source=source,
                    evidence={"text":str(logo.get("evidence") or "")[:1000]},
                )

        for club in result.get("clubs",[]) if isinstance(result.get("clubs"),list) else []:
            if not isinstance(club,dict) or not str(club.get("name") or "").strip():
                continue
            label = str(club["name"]).strip()[:200]
            predicted_school = str(club.get("school") or "").strip()
            predicted_school_id = store.ensure_school(predicted_school) if predicted_school else asset.get("school_id")
            entity_id = ""
            if predicted_school_id:
                entity_id = store.ensure_club(predicted_school_id,label,source={"type":source,"evidence":club.get("evidence")},confirmed=False) or ""
            store.add_observation(
                asset_id,"club",label=label,entity_id=entity_id,status="possible",
                confidence=_confidence(club.get("confidence")),source=source,
                evidence={"school_candidate":predicted_school,"text":str(club.get("evidence") or "")[:1000]},
            )

        raw_dates = result.get("dates",[]) if isinstance(result.get("dates"),list) else []
        for date in raw_dates:
            if isinstance(date,dict):
                store.add_date(
                    asset_id,str(date.get("value") or ""),source="ocr",confidence=_confidence(date.get("confidence")),
                    evidence=str(date.get("evidence") or ""),status="possible",
                )

        event = result.get("event") if isinstance(result.get("event"),dict) else {}
        event_name = str(event.get("name") or "").strip()
        if event_name:
            event_date = str(raw_dates[0].get("value") or "") if raw_dates and isinstance(raw_dates[0],dict) else ""
            event_id = store.ensure_event(
                school_id=asset.get("school_id"),club_id=asset.get("club_id"),name=event_name,
                event_type=str(event.get("type") or ""),event_date=event_date,
                start_time=str(event.get("start_time") or ""),end_time=str(event.get("end_time") or ""),
                location=str(event.get("location") or ""),status="possible",
                evidence=[{"source":source,"asset_id":asset_id,"text":event.get("evidence")}],
            )
            store.add_observation(
                asset_id,"event",label=event_name,entity_id=event_id,status="possible",
                confidence=_confidence(event.get("confidence")),source=source,
                evidence={"text":str(event.get("evidence") or "")[:1000]},
            )

        # 人物比對只讀「已確認人物＋已確認參考圖」。結果仍一律 possible。
        references = []
        for ref in store.confirmed_person_references(user_id):
            try:
                references.append({
                    "person_id":ref["person_id"],"name":ref["name"],
                    "data_url":_data_url(ref["storage_path"],ref["mime_type"]),
                })
            except OSError:
                continue
        if references and int(people.get("count") or len(descriptions) or 0) > 0:
            store.update_job(job_id,status="running",stage="confirmed_people_comparison",progress=78)
            for match in await fal.compare_confirmed_people(target_url,references):
                ref = next((r for r in references if r["person_id"] == match.get("person_id")),None)
                confidence = _confidence(match.get("confidence"))
                if ref and confidence >= 0.55:
                    store.add_observation(
                        asset_id,"person",label=f"可能是{ref['name']}",entity_id=ref["person_id"],status="possible",
                        confidence=confidence,source=source,
                        evidence={"text":str(match.get("evidence") or "")[:1000],"requires_user_confirmation":True},
                    )

        store.set_analysis(asset_id,result,status="complete")
        store.update_job(job_id,status="complete",stage="complete",progress=100)
        store.record_learning(user_id,"analysis_completed",asset_id=asset_id,payload={"model":config.FAL_VISION_MODEL},outcome="success")
        return store.get_asset(asset_id,user_id) or {}
    except fal.FalError as exc:
        partial = "needs_vision_config" if exc.code == "vision_not_configured" else "analysis_failed"
        store.mark_analysis_status(asset_id,partial)
        store.update_job(job_id,status="failed",stage="vision",progress=100,error_code=exc.code,error_message=str(exc))
        store.record_learning(user_id,"tool_failed",asset_id=asset_id,payload={"tool":"vision","error_code":exc.code},outcome="failed")
        raise
    except Exception as exc:
        store.mark_analysis_status(asset_id,"analysis_failed")
        store.update_job(job_id,status="failed",stage="unknown",progress=100,error_code="analysis_error",error_message="圖片分析失敗")
        store.record_learning(user_id,"tool_failed",asset_id=asset_id,payload={"tool":"vision","error_code":"analysis_error"},outcome="failed")
        raise VisualAssetError("圖片分析失敗，原圖與本機品質檢查結果仍已保留",code="analysis_error") from exc
