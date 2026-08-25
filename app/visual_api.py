"""視覺資料庫 HTTP API；沿用既有 cookie 身分、限流、CSRF 與 audit。"""

from __future__ import annotations

import contextlib
from typing import Any, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from . import config
from .services import audit, auth, fal, permissions
from .services.visual_analysis import analyze_asset
from .services.visual_assets import VisualAssetError, get_visual_store


router = APIRouter(prefix="/api", dependencies=[Depends(auth.require_user)], tags=["visual-assets"])


def _http_error(exc: VisualAssetError) -> HTTPException:
    status = 404 if exc.code == "not_found" else 413 if exc.code == "file_too_large" else 409 if exc.code in {"school_conflict","immutable_field"} else 422
    return HTTPException(status_code=status,detail={"code":exc.code,"message":str(exc)})


async def _safe_analyze(asset_id: str, user_id: str) -> None:
    with contextlib.suppress(Exception):
        await analyze_asset(asset_id,user_id)


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AssetPatch(StrictRequest):
    privacy: Literal["private","shared","public"] | None = None
    commercial_use: Literal["allowed","not_allowed","unknown"] | None = None
    source: str | None = Field(default=None,max_length=120)
    source_metadata: dict[str,Any] | None = None
    review_status: Literal["confirmed","possible","pending","conflict","ignored"] | None = None
    school: str | None = Field(default=None,max_length=120)
    club: str | None = Field(default=None,max_length=160)


class AnalyzeRequest(StrictRequest):
    asset_ids: list[str] = Field(min_length=1,max_length=30)


class ConfirmRequest(StrictRequest):
    asset_id: str = Field(min_length=8,max_length=80)
    entity_type: Literal["person","scene","event","club","date"]
    action: Literal["confirm","correct","ignore"]
    entity_id: str = Field(default="",max_length=80)
    candidate_label: str = Field(default="",max_length=300)
    observation_id: str = Field(default="",max_length=80)
    values: dict[str,Any] = Field(default_factory=dict)
    reason: str = Field(default="",max_length=1000)


class UsageRequest(StrictRequest):
    action: Literal["selected","excluded","used","downloaded","modified","storyboard","social_post","poster"]
    context: dict[str,Any] = Field(default_factory=dict)


class ExportRequest(StrictRequest):
    asset_ids: list[str] = Field(min_length=1,max_length=100)
    rendition: Literal["original","thumbnail","1:1","4:5","9:16","16:9"] = "original"


class ApproveLearningRequest(StrictRequest):
    correction_id: str = Field(min_length=8,max_length=80)


@router.post("/visual-assets/upload",status_code=201)
async def upload_visual_assets(
    request: Request,
    background: BackgroundTasks,
    files: list[UploadFile] = File(...),
    school: str = Form(default="",max_length=120),
    club: str = Form(default="",max_length=160),
    source: str = Form(default="upload",max_length=120),
    privacy: Literal["private","shared","public"] = Form(default="private"),
    commercial_use: Literal["allowed","not_allowed","unknown"] = Form(default="unknown"),
    file_created_date: str = Form(default="",max_length=40),
    auto_analyze: bool = Form(default=True),
    user_id: str = Depends(auth.require_user),
):
    if not files:
        raise HTTPException(status_code=422,detail="至少選擇一張圖片")
    if len(files) > config.VISUAL_MAX_BATCH:
        raise HTTPException(status_code=413,detail=f"一次最多上傳 {config.VISUAL_MAX_BATCH} 張圖片")
    store = get_visual_store()
    created: list[dict[str,Any]] = []
    errors: list[dict[str,str]] = []
    for upload in files:
        filename = (upload.filename or "unnamed-image")[:240]
        mime_type = (upload.content_type or "").lower()
        try:
            content = await upload.read(config.VISUAL_MAX_FILE_BYTES + 1)
            if len(content) > config.VISUAL_MAX_FILE_BYTES:
                raise VisualAssetError(f"圖片超過 {config.VISUAL_MAX_FILE_BYTES // 1_000_000}MB 上限",code="file_too_large")
            item = store.create_asset(
                user_id=user_id,filename=filename,mime_type=mime_type,content=content,source=source,
                school=school,club=club,privacy=privacy,commercial_use=commercial_use,
                file_created_date=file_created_date,source_metadata={"upload_content_type":mime_type},
            )
            created.append(item)
            if auto_analyze:
                background.add_task(_safe_analyze,item["asset_id"],user_id)
        except VisualAssetError as exc:
            errors.append({"filename":filename,"code":exc.code,"message":str(exc)})
        finally:
            await upload.close()
    audit.write_audit(
        action="visual.upload",actor_user_id=user_id,resource="visual-assets",
        detail={"created":len(created),"errors":[e["code"] for e in errors],"school":school,"club":club},request=request,ok=bool(created),
    )
    payload = {"items":created,"errors":errors,"created":len(created),"analysis_scheduled":bool(auto_analyze and created)}
    if not created:
        status = 413 if errors and all(e["code"] == "file_too_large" for e in errors) else 422
        return JSONResponse(payload,status_code=status)
    return payload


@router.post("/visual-assets/analyze")
async def analyze_visual_assets(req: AnalyzeRequest, request: Request, user_id: str = Depends(auth.require_user)) -> dict[str,Any]:
    items,errors = [],[]
    for asset_id in list(dict.fromkeys(req.asset_ids)):
        try:
            items.append(await analyze_asset(asset_id,user_id))
        except fal.FalError as exc:
            errors.append({"asset_id":asset_id,"code":exc.code,"message":str(exc)})
        except VisualAssetError as exc:
            errors.append({"asset_id":asset_id,"code":exc.code,"message":str(exc)})
    audit.write_audit(action="visual.analyze",actor_user_id=user_id,resource="visual-assets",detail={"completed":len(items),"errors":[e["code"] for e in errors]},request=request,ok=bool(items))
    return {"items":items,"errors":errors,"completed":len(items)}


@router.get("/visual-assets/dashboard")
async def visual_dashboard(user_id: str = Depends(auth.require_user)) -> dict[str,Any]:
    return get_visual_store().dashboard(user_id)


@router.get("/visual-assets/search")
async def search_visual_assets(
    request: Request,
    q: str = Query(default="",max_length=1000),
    school: str = Query(default="",max_length=120), club: str = Query(default="",max_length=160),
    person: str = Query(default="",max_length=160), scene: str = Query(default="",max_length=100),
    event: str = Query(default="",max_length=200), date_from: str = Query(default="",max_length=10),
    date_to: str = Query(default="",max_length=10), ratio: str = Query(default="",pattern=r"^(|1:1|4:5|9:16|16:9|landscape|portrait)$"),
    quality_min: float = Query(default=0,ge=0,le=100),
    commercial_use: str = Query(default="",pattern=r"^(|allowed|not_allowed|unknown)$"),
    privacy: str = Query(default="",pattern=r"^(|private|shared|public)$"),
    duplicate: str = Query(default="",pattern=r"^(|only|exclude)$"),
    page: int = Query(default=1,ge=1,le=10000), limit: int = Query(default=30,ge=1),
    user_id: str = Depends(auth.require_user),
) -> dict[str,Any]:
    capped = min(limit,config.VISUAL_SEARCH_LIMIT)
    store = get_visual_store()
    items,total,parsed = store.search(
        user_id,query=q,page=page,limit=capped,school=school,club=club,person=person,scene=scene,event=event,
        date_from=date_from,date_to=date_to,ratio=ratio,quality_min=quality_min,commercial_use=commercial_use,
        privacy=privacy,duplicate=duplicate,
    )
    store.record_learning(user_id,"search",query=q,payload={"filters":parsed,"result_ids":[i["asset_id"] for i in items]},outcome=str(total))
    audit.write_audit(action="visual.search",actor_user_id=user_id,resource="visual-assets",detail={"query":q[:200],"count":total},request=request)
    return {"items":items,"total":total,"page":page,"limit":capped,"parsed_conditions":parsed}


@router.post("/visual-assets/search-by-image")
async def search_visual_assets_by_image(
    request: Request,file: UploadFile = File(...),page: int = Query(default=1,ge=1),limit: int = Query(default=30,ge=1),
    user_id: str = Depends(auth.require_user),
) -> dict[str,Any]:
    try:
        content = await file.read(config.VISUAL_MAX_FILE_BYTES + 1)
        if len(content) > config.VISUAL_MAX_FILE_BYTES:
            raise VisualAssetError("查詢圖片超過大小限制",code="file_too_large")
        items,total = get_visual_store().search_by_image(user_id,content,(file.content_type or "").lower(),page=page,limit=min(limit,config.VISUAL_SEARCH_LIMIT))
    except VisualAssetError as exc:
        raise _http_error(exc) from exc
    finally:
        await file.close()
    get_visual_store().record_learning(user_id,"search_by_image",payload={"result_ids":[i["asset_id"] for i in items]},outcome=str(total))
    audit.write_audit(action="visual.search_by_image",actor_user_id=user_id,resource="visual-assets",detail={"count":total},request=request)
    return {"items":items,"total":total,"page":page,"limit":min(limit,config.VISUAL_SEARCH_LIMIT)}


@router.get("/visual-assets/{asset_id}")
async def get_visual_asset(asset_id: str,user_id: str = Depends(auth.require_user)) -> dict[str,Any]:
    item = get_visual_store().get_asset(asset_id,user_id)
    if not item:
        raise HTTPException(status_code=404,detail="找不到圖片或沒有權限")
    return item


@router.patch("/visual-assets/{asset_id}")
async def patch_visual_asset(asset_id: str,req: AssetPatch,request: Request,user_id: str = Depends(auth.require_user)) -> dict[str,Any]:
    fields = req.model_dump(exclude_unset=True)
    try:
        item = get_visual_store().patch_asset(asset_id,user_id,fields)
    except VisualAssetError as exc:
        raise _http_error(exc) from exc
    if not item:
        raise HTTPException(status_code=404,detail="找不到可修改的圖片")
    get_visual_store().add_correction(user_id,asset_id,"asset_metadata",{},fields,"使用者修改資產資料")
    audit.write_audit(action="visual.patch",actor_user_id=user_id,resource=asset_id,detail={"fields":sorted(fields)},request=request)
    return item


@router.get("/visual-assets/{asset_id}/file")
async def visual_asset_file(
    asset_id: str,request: Request,variant: str = Query(default="original"),download: bool = Query(default=False),
    user_id: str = Depends(auth.require_user),
):
    try:
        resolved = get_visual_store().asset_file(asset_id,user_id,variant)
    except VisualAssetError as exc:
        raise _http_error(exc) from exc
    if not resolved:
        raise HTTPException(status_code=404,detail="找不到圖片或沒有權限")
    path,mime,filename = resolved
    if not path.is_file():
        raise HTTPException(status_code=404,detail="圖片檔案目前無法取得")
    if download:
        get_visual_store().record_learning(user_id,"downloaded",asset_id=asset_id,payload={"variant":variant},outcome="download")
    audit.write_audit(action="visual.download" if download else "visual.view",actor_user_id=user_id,resource=asset_id,detail={"variant":variant},request=request)
    return FileResponse(path,media_type=mime,filename=filename if download else None)


@router.post("/visual-assets/{asset_id}/usage")
async def record_visual_usage(asset_id: str,req: UsageRequest,user_id: str = Depends(auth.require_user)) -> dict[str,bool]:
    if not get_visual_store().visible_asset(asset_id,user_id):
        raise HTTPException(status_code=404,detail="找不到圖片或沒有權限")
    get_visual_store().record_learning(user_id,req.action,asset_id=asset_id,payload=req.context,outcome="recorded")
    return {"ok":True}


@router.post("/visual-assets/export")
async def export_visual_assets(req: ExportRequest,request: Request,user_id: str = Depends(auth.require_user)):
    try:
        path = get_visual_store().export_package(user_id,req.asset_ids,rendition=req.rendition)
    except VisualAssetError as exc:
        raise _http_error(exc) from exc
    for asset_id in req.asset_ids:
        get_visual_store().record_learning(user_id,"downloaded",asset_id=asset_id,payload={"kind":"material_pack","rendition":req.rendition},outcome="export")
    audit.write_audit(action="visual.export",actor_user_id=user_id,resource="visual-assets",detail={"count":len(req.asset_ids),"rendition":req.rendition},request=request)
    return FileResponse(path,media_type="application/zip",filename=path.name)


@router.post("/entities/confirm")
async def confirm_visual_entity(req: ConfirmRequest,request: Request,user_id: str = Depends(auth.require_user)) -> dict[str,Any]:
    try:
        item = get_visual_store().confirm_entity(user_id=user_id,**req.model_dump())
    except VisualAssetError as exc:
        raise _http_error(exc) from exc
    audit.write_audit(action="visual.entity_confirm",actor_user_id=user_id,resource=req.asset_id,detail={"type":req.entity_type,"action":req.action,"entity_id":req.entity_id},request=request)
    return item


async def _entity_list(kind: str,query: str,school: str,page: int,limit: int) -> dict[str,Any]:
    capped = min(limit,config.VISUAL_SEARCH_LIMIT)
    items,total = get_visual_store().list_entities(kind,query=query,school=school,page=page,limit=capped)
    return {"items":items,kind:items,"total":total,"page":page,"limit":capped}


@router.get("/people")
async def list_people(q: str = Query(default="",max_length=200),school: str = Query(default="",max_length=120),page: int = Query(default=1,ge=1),limit: int = Query(default=30,ge=1)):
    return await _entity_list("people",q,school,page,limit)


@router.get("/clubs")
async def list_clubs(q: str = Query(default="",max_length=200),school: str = Query(default="",max_length=120),page: int = Query(default=1,ge=1),limit: int = Query(default=30,ge=1)):
    return await _entity_list("clubs",q,school,page,limit)


@router.get("/events")
async def list_events(q: str = Query(default="",max_length=200),school: str = Query(default="",max_length=120),page: int = Query(default=1,ge=1),limit: int = Query(default=30,ge=1)):
    return await _entity_list("events",q,school,page,limit)


@router.get("/scenes")
async def list_scenes(q: str = Query(default="",max_length=200),page: int = Query(default=1,ge=1),limit: int = Query(default=30,ge=1)):
    return await _entity_list("scenes",q,"",page,limit)


@router.get("/learning/insights")
async def learning_insights(user_id: str = Depends(auth.require_user)) -> dict[str,Any]:
    return get_visual_store().learning_insights(user_id)


@router.get("/learning/corrections")
async def learning_corrections(page: int = Query(default=1,ge=1),limit: int = Query(default=30,ge=1),user_id: str = Depends(auth.require_user)) -> dict[str,Any]:
    capped = min(limit,config.VISUAL_SEARCH_LIMIT)
    items,total = get_visual_store().corrections(user_id,page=page,limit=capped)
    return {"items":items,"total":total,"page":page,"limit":capped}


@router.post("/learning/approve")
async def approve_learning(req: ApproveLearningRequest,request: Request,perms: permissions.Permissions = Depends(permissions.require_manage)) -> dict[str,Any]:
    item = get_visual_store().approve_correction(req.correction_id,perms.user_id or "")
    if not item:
        raise HTTPException(status_code=404,detail="找不到修正紀錄")
    audit.write_audit(action="visual.learning_approve",actor_user_id=perms.user_id,resource=req.correction_id,request=request)
    return item
