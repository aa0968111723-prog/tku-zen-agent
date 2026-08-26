"""視覺資料庫 HTTP API；沿用既有 cookie 身分、限流、CSRF 與 audit。"""

from __future__ import annotations

import contextlib
import json
import logging
import mimetypes
from typing import Any, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from . import config
from .services import audit, auth, fal, permissions
from .services.visual_analysis import analyze_asset
from .services.visual_assets import VisualAssetError, get_visual_store
from .services.insforge_adapters import BLOCKED_BY_EXTERNAL_DEPENDENCY, InsForgeUnavailable, get_insforge_adapters
from .services.insforge_sync import InsForgeSyncAdapter, resolved_insforge_owner
from .services.insforge_data_sync import InsForgeDataSyncAdapter
from .services.data_organization import DataOrganizationService
from .services.library_context import LibraryContextResolver


router = APIRouter(prefix="/api", dependencies=[Depends(auth.require_user)], tags=["visual-assets"])
logger = logging.getLogger(__name__)


def _http_error(exc: VisualAssetError) -> HTTPException:
    status = 404 if exc.code == "not_found" else 413 if exc.code == "file_too_large" else 409 if exc.code in {"school_conflict","immutable_field","date_conflict","resync_required"} else 422
    return HTTPException(status_code=status,detail={"code":exc.code,"message":str(exc)})


async def _safe_analyze(asset_id: str, user_id: str) -> None:
    try:
        await analyze_asset(asset_id, user_id)
    except Exception:
        logger.warning("background visual analysis failed asset_id=%s", asset_id, exc_info=True)


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AssetPatch(StrictRequest):
    privacy: Literal["private","shared","public"] | None = None
    commercial_use: Literal["allowed","not_allowed","unknown"] | None = None
    source: str | None = Field(default=None,max_length=120)
    source_metadata: dict[str,Any] | None = None
    review_status: Literal["verified","probable","pending_review","conflicted","failed"] | None = None
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


class CollectionAssetRequest(StrictRequest):
    asset_id: str = Field(min_length=8,max_length=80)
    note: str = Field(default="",max_length=1000)
    rendition: Literal["original","thumbnail","1:1","4:5","9:16","16:9"] = "original"


class ApproveLearningRequest(StrictRequest):
    correction_id: str = Field(min_length=8,max_length=80)


class AssetConfirmRequest(StrictRequest):
    reason: str = Field(default="使用者確認整筆素材",max_length=1000)


class VisualSyncRequest(StrictRequest):
    asset_ids: list[str] = Field(min_length=1,max_length=100)
    project_id: str = Field(default="",max_length=160)
    idempotency_key: str = Field(min_length=8,max_length=160)
    resumed_from: str = Field(default="",max_length=80)


class VisualSyncRetryRequest(StrictRequest):
    asset_ids: list[str] | None = Field(default=None,max_length=100)


class BackendSyncRequest(StrictRequest):
    resource_types: list[Literal["projects","artifacts","activities","research_sources","knowledge","visual_assets","organization"]] = Field(
        default_factory=lambda: ["projects","artifacts","activities","research_sources","knowledge","visual_assets","organization"],
        min_length=1,max_length=7,
    )
    project_id: str = Field(default="",max_length=160)
    idempotency_key: str = Field(min_length=8,max_length=160)
    dry_run: bool = False


class InventoryRequest(StrictRequest):
    project_id: str = Field(default="",max_length=160)


class OrganizeRequest(StrictRequest):
    project_id: str = Field(default="",max_length=160)
    idempotency_key: str = Field(min_length=8,max_length=120)


class ContextNodeRequest(StrictRequest):
    project_id: str = Field(min_length=1,max_length=160)
    node_type: Literal["story","scene","shot","output"]
    title: str = Field(min_length=1,max_length=240)
    position: int = Field(default=0,ge=0,le=10000)
    parent_id: str = Field(default="",max_length=160)
    requirements: dict[str,Any] = Field(default_factory=dict)


class ContextSearchRequest(StrictRequest):
    query: str = Field(min_length=1,max_length=1000)
    project_id: str = Field(default="",max_length=160)
    scene_position: int = Field(default=0,ge=0,le=10000)
    shot_position: int = Field(default=0,ge=0,le=10000)
    page: int = Field(default=1,ge=1)
    limit: int = Field(default=30,ge=1,le=60)


def _media_type(upload: UploadFile) -> str:
    supplied = (upload.content_type or "").lower()
    if supplied and supplied != "application/octet-stream":
        return supplied
    return (mimetypes.guess_type(upload.filename or "")[0] or supplied).lower()


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
    idempotency_key: str = Form(default="",max_length=160),
    project_id: str = Form(default="",max_length=160),
    user_id: str = Depends(auth.require_user),
):
    if not files:
        raise HTTPException(status_code=422,detail="至少選擇一張圖片")
    if len(files) > config.VISUAL_MAX_BATCH:
        raise HTTPException(status_code=413,detail=f"一次最多上傳 {config.VISUAL_MAX_BATCH} 張圖片")
    store = get_visual_store()
    upload_job = None
    if idempotency_key:
        generated_manifest = {"total_count":len(files),"items":[{"client_key":f"{i}:{f.filename or 'unnamed'}","relative_path":f.filename or "unnamed"} for i,f in enumerate(files)]}
        upload_job = store.start_import(user_id,idempotency_key=idempotency_key,root_name="direct-upload",manifest=generated_manifest,total_count=len(files))
    created: list[dict[str,Any]] = []
    skipped_count = 0
    errors: list[dict[str,str]] = []
    for index,upload in enumerate(files):
        filename = (upload.filename or "unnamed-image")[:240]
        mime_type = _media_type(upload)
        try:
            was_skipped = False
            content = await upload.read(config.VISUAL_MAX_FILE_BYTES + 1)
            if len(content) > config.VISUAL_MAX_FILE_BYTES and not upload_job:
                raise VisualAssetError(f"圖片超過 {config.VISUAL_MAX_FILE_BYTES // 1_000_000}MB 上限",code="file_too_large")
            if upload_job:
                item,was_skipped = store.ingest_import_item(
                    user_id=user_id,import_id=upload_job["id"],client_key=f"{index}:{filename}",relative_path=filename,
                    filename=filename,mime_type=mime_type,content=content,last_modified=file_created_date,
                    school=school,club=club,source=source,privacy=privacy,commercial_use=commercial_use,
                    project_id=project_id,
                )
            else:
                item = store.create_asset(
                    user_id=user_id,filename=filename,mime_type=mime_type,content=content,source=source,
                    school=school,club=club,privacy=privacy,commercial_use=commercial_use,
                    file_created_date=file_created_date,source_metadata={"upload_content_type":mime_type},
                    project_id=project_id,
                )
            created.append(item)
            skipped_count += int(was_skipped)
            if auto_analyze and not was_skipped:
                background.add_task(_safe_analyze,item["asset_id"],user_id)
        except VisualAssetError as exc:
            errors.append({"filename":filename,"code":exc.code,"message":str(exc)})
        finally:
            await upload.close()
    audit.write_audit(
        action="visual.upload",actor_user_id=user_id,resource="visual-assets",
        detail={"created":len(created),"errors":[e["code"] for e in errors],"school":school,"club":club},request=request,ok=bool(created),
    )
    payload = {"items":created,"errors":errors,"created":len(created) - skipped_count,"skipped":skipped_count,"analysis_scheduled":bool(auto_analyze and created and skipped_count < len(created)),"import_id":upload_job["id"] if upload_job else ""}
    if not created:
        status = 413 if errors and all(e["code"] == "file_too_large" for e in errors) else 422
        return JSONResponse(payload,status_code=status)
    return payload


@router.post("/visual-assets/import",status_code=201)
async def import_visual_assets(
    request: Request, background: BackgroundTasks, files: list[UploadFile] = File(...),
    manifest: str = Form(...,max_length=200_000), idempotency_key: str = Form(...,min_length=8,max_length=160),
    root_name: str = Form(default="folder",max_length=240), school: str = Form(default="",max_length=120),
    club: str = Form(default="",max_length=160), source: str = Form(default="folder_import",max_length=120),
    privacy: Literal["private","shared","public"] = Form(default="private"),
    commercial_use: Literal["allowed","not_allowed","unknown"] = Form(default="unknown"),
    resync: bool = Form(default=False), auto_analyze: bool = Form(default=True),
    user_id: str = Depends(auth.require_user),
):
    if len(files) > config.VISUAL_MAX_BATCH:
        raise HTTPException(status_code=413,detail=f"一次最多匯入 {config.VISUAL_MAX_BATCH} 個素材；其餘可沿用 import_id 續傳")
    try:
        data = json.loads(manifest)
        entries = data.get("items") if isinstance(data,dict) else None
        if not isinstance(entries,list):
            raise ValueError
    except (ValueError,TypeError,json.JSONDecodeError) as exc:
        raise HTTPException(status_code=422,detail={"code":"invalid_manifest","message":"manifest 必須含 items 陣列"}) from exc
    if len(entries) < len(files):
        raise HTTPException(status_code=422,detail={"code":"manifest_file_mismatch","message":"manifest 項目少於本次檔案數"})
    store = get_visual_store()
    job = store.start_import(user_id,idempotency_key=idempotency_key,root_name=root_name,manifest=data,total_count=int(data.get("total_count") or len(entries)))
    created,errors,skipped = [],[],[]
    for index,upload in enumerate(files):
        entry = entries[index] if isinstance(entries[index],dict) else {}
        filename = (upload.filename or str(entry.get("filename") or "unnamed"))[:240]
        try:
            content = await upload.read(config.VISUAL_MAX_FILE_BYTES + 1)
            asset,was_skipped = store.ingest_import_item(
                user_id=user_id,import_id=job["id"],client_key=str(entry.get("client_key") or entry.get("relative_path") or filename),
                relative_path=str(entry.get("relative_path") or filename),filename=filename,mime_type=_media_type(upload),content=content,
                last_modified=str(entry.get("last_modified") or ""),school=school,club=club,source=source,
                privacy=privacy,commercial_use=commercial_use,resync=resync,
            )
            (skipped if was_skipped else created).append(asset)
            if auto_analyze and not was_skipped:
                background.add_task(_safe_analyze,asset["asset_id"],user_id)
        except VisualAssetError as exc:
            errors.append({"filename":filename,"client_key":str(entry.get("client_key") or ""),"code":exc.code,"message":str(exc)})
        finally:
            await upload.close()
    current = store.get_import(job["id"],user_id) or job
    audit.write_audit(action="visual.import",actor_user_id=user_id,resource=job["id"],detail={"created":len(created),"skipped":len(skipped),"failed":len(errors),"resync":resync},request=request,ok=bool(created or skipped))
    payload = {"import":current,"items":created,"skipped":skipped,"errors":errors,"partial_failure":bool(errors and (created or skipped))}
    return JSONResponse(payload,status_code=201 if created or skipped else 422)


@router.get("/visual-imports/{import_id}")
async def get_visual_import(import_id: str,user_id: str = Depends(auth.require_user)) -> dict[str,Any]:
    item = get_visual_store().get_import(import_id,user_id)
    if not item:
        raise HTTPException(status_code=404,detail="找不到匯入工作或沒有權限")
    return item


@router.post("/visual-assets/analyze")
async def analyze_visual_assets(req: AnalyzeRequest, request: Request, user_id: str = Depends(auth.require_user)) -> dict[str,Any]:
    items,errors = [],[]
    for asset_id in list(dict.fromkeys(req.asset_ids)):
        try:
            items.append(await analyze_asset(asset_id,user_id))
        except fal.FalError as exc:
            errors.append({"asset_id":asset_id,"code":"BLOCKED_BY_EXTERNAL_DEPENDENCY","provider_code":exc.code,"message":str(exc)})
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
    people_min: int = Query(default=0,ge=0,le=1000),
    brightness_min: float = Query(default=0,ge=0,le=100),
    verification_status: str = Query(default="",pattern=r"^(|verified|probable|pending_review|conflicted|failed)$"),
    page: int = Query(default=1,ge=1,le=10000), limit: int = Query(default=30,ge=1,le=100),
    user_id: str = Depends(auth.require_user),
) -> dict[str,Any]:
    capped = min(limit,config.VISUAL_SEARCH_LIMIT)
    store = get_visual_store()
    items,total,parsed = store.search(
        user_id,query=q,page=page,limit=capped,school=school,club=club,person=person,scene=scene,event=event,
        date_from=date_from,date_to=date_to,ratio=ratio,quality_min=quality_min,commercial_use=commercial_use,
        privacy=privacy,duplicate=duplicate,people_min=people_min,brightness_min=brightness_min,
        verification_status=verification_status,
    )
    store.record_learning(user_id,"search",query=q,payload={"filters":parsed,"result_ids":[i["asset_id"] for i in items]},outcome=str(total))
    audit.write_audit(action="visual.search",actor_user_id=user_id,resource="visual-assets",detail={"query":q[:200],"count":total},request=request)
    backend: dict[str,Any] = {"name":"local","status":"active"}
    # Optional remote ranking is additive.  ACL remains local-first: only
    # assets already visible to this user can be hydrated into the response.
    if config.INSFORGE_SYNC_MODE in {"dual", "insforge"}:
        try:
            remote = get_insforge_adapters().search.search({"query": q[:1000], "filters": parsed, "owner_id": resolved_insforge_owner(store, user_id) or user_id, "page": page, "limit": capped})
            local_by_id = {str(item["asset_id"]): item for item in items}
            remote_items: list[dict[str,Any]] = []
            for row in remote:
                asset_id = str(row.get("asset_id") or row.get("id") or "")
                if not asset_id:
                    continue
                hydrated = local_by_id.get(asset_id) or store.get_asset(asset_id,user_id)
                if hydrated:
                    hydrated = dict(hydrated)
                    hydrated["match_reason"] = row.get("match_reason") or row.get("reason") or "InsForge hybrid search"
                    hydrated["remote_score"] = row.get("score")
                    remote_items.append(hydrated)
            if remote_items:
                seen = {str(item["asset_id"]) for item in remote_items}
                items = remote_items + [item for item in items if str(item["asset_id"]) not in seen]
                items = items[:capped]
            backend = {"name":"insforge","status":"active","remote_count":len(remote_items)}
        except InsForgeUnavailable as exc:
            backend = {"name":"insforge","status":"blocked","code":BLOCKED_BY_EXTERNAL_DEPENDENCY,"message":str(exc),"fallback":"local"}
    return {"items":items,"total":total,"page":page,"limit":capped,"parsed_conditions":parsed,"backend":backend}


@router.post("/visual-assets/search-by-image")
async def search_visual_assets_by_image(
    request: Request,file: UploadFile = File(...),page: int = Query(default=1,ge=1,le=10000),limit: int = Query(default=30,ge=1,le=100),
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


@router.get("/visual-assets/review-queue")
async def visual_review_queue(
    status: str = Query(default="",pattern=r"^(|verified|probable|pending_review|conflicted|failed)$"),
    page: int = Query(default=1,ge=1,le=10000),limit: int = Query(default=30,ge=1,le=100),
    user_id: str = Depends(auth.require_user),
) -> dict[str,Any]:
    capped = min(limit,config.VISUAL_SEARCH_LIMIT)
    items,total = get_visual_store().review_queue(user_id,status=status,page=page,limit=capped)
    return {"items":items,"total":total,"page":page,"limit":capped,"status":status or "needs_review"}


@router.post("/visual-assets/{asset_id}/analyze")
async def analyze_one_visual_asset(asset_id: str,request: Request,user_id: str = Depends(auth.require_user)) -> dict[str,Any]:
    try:
        item = await analyze_asset(asset_id,user_id)
    except fal.FalError as exc:
        raise HTTPException(status_code=503,detail={"code":"BLOCKED_BY_EXTERNAL_DEPENDENCY","provider_code":exc.code,"message":str(exc)}) from exc
    except VisualAssetError as exc:
        raise _http_error(exc) from exc
    audit.write_audit(action="visual.analyze",actor_user_id=user_id,resource=asset_id,request=request)
    return item


@router.post("/visual-assets/{asset_id}/retry")
async def retry_visual_asset(asset_id: str,request: Request,user_id: str = Depends(auth.require_user)) -> dict[str,Any]:
    if not get_visual_store().owned_asset(asset_id,user_id):
        raise HTTPException(status_code=404,detail="找不到可重試的素材")
    try:
        item = await analyze_asset(asset_id,user_id)
    except fal.FalError as exc:
        raise HTTPException(status_code=503,detail={"code":"BLOCKED_BY_EXTERNAL_DEPENDENCY","provider_code":exc.code,"message":str(exc)}) from exc
    except VisualAssetError as exc:
        raise _http_error(exc) from exc
    audit.write_audit(action="visual.retry",actor_user_id=user_id,resource=asset_id,request=request)
    return item


@router.post("/visual-assets/{asset_id}/confirm")
async def confirm_visual_asset(asset_id: str,req: AssetConfirmRequest,request: Request,user_id: str = Depends(auth.require_user)) -> dict[str,Any]:
    try:
        item = get_visual_store().confirm_asset(asset_id,user_id,reason=req.reason)
    except VisualAssetError as exc:
        raise _http_error(exc) from exc
    audit.write_audit(action="visual.asset_confirm",actor_user_id=user_id,resource=asset_id,request=request)
    return item


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
    asset_id: str,request: Request,variant: str = Query(default="original",pattern=r"^(original|thumbnail|1:1|4:5|9:16|16:9)$"),download: bool = Query(default=False),
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
async def record_visual_usage(asset_id: str,req: UsageRequest,request: Request,user_id: str = Depends(auth.require_user)) -> dict[str,Any]:
    if not get_visual_store().visible_asset(asset_id,user_id):
        raise HTTPException(status_code=404,detail="找不到圖片或沒有權限")
    get_visual_store().record_learning(user_id,req.action,asset_id=asset_id,payload=req.context,outcome="recorded")
    collection = None
    if req.action in {"storyboard","social_post","poster"}:
        collection = get_visual_store().add_to_collection(
            user_id,asset_id,kind=req.action,note=str(req.context.get("note") or ""),
            rendition=str(req.context.get("rendition") or "original"),
        )
    audit.write_audit(action="visual.usage",actor_user_id=user_id,resource=asset_id,detail={"action":req.action},request=request)
    return {"ok":True,"collection":collection}


@router.get("/visual-collections")
async def list_visual_collections(
    kind: str = Query(default="",pattern=r"^(|material_pack|storyboard|social_post|poster)$"),
    page: int = Query(default=1,ge=1),limit: int = Query(default=30,ge=1,le=100),
    user_id: str = Depends(auth.require_user),
) -> dict[str,Any]:
    capped = min(limit,config.VISUAL_SEARCH_LIMIT)
    items,total = get_visual_store().list_collections(user_id,kind=kind,page=page,limit=capped)
    return {"items":items,"total":total,"page":page,"limit":capped}


@router.get("/visual-collections/{collection_id}")
async def get_visual_collection(collection_id: str,user_id: str = Depends(auth.require_user)) -> dict[str,Any]:
    item = get_visual_store().get_collection(collection_id,user_id)
    if not item:
        raise HTTPException(status_code=404,detail="找不到素材集合")
    return item


@router.post("/visual-collections/{collection_id}/assets")
async def add_visual_collection_asset(
    collection_id: str,req: CollectionAssetRequest,user_id: str = Depends(auth.require_user),
) -> dict[str,Any]:
    current = get_visual_store().get_collection(collection_id,user_id)
    if not current:
        raise HTTPException(status_code=404,detail="找不到素材集合")
    try:
        return get_visual_store().add_to_collection(
            user_id,req.asset_id,kind=current["kind"],name=current["name"],note=req.note,rendition=req.rendition,
        )
    except VisualAssetError as exc:
        raise _http_error(exc) from exc


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


async def _entity_list(kind: str,query: str,school: str,page: int,limit: int,user_id: str) -> dict[str,Any]:
    capped = min(limit,config.VISUAL_SEARCH_LIMIT)
    items,total = get_visual_store().list_entities(kind,user_id=user_id,query=query,school=school,page=page,limit=capped)
    return {"items":items,kind:items,"total":total,"page":page,"limit":capped}


@router.get("/people")
async def list_people(q: str = Query(default="",max_length=200),school: str = Query(default="",max_length=120),page: int = Query(default=1,ge=1),limit: int = Query(default=30,ge=1,le=100),user_id: str = Depends(auth.require_user)):
    return await _entity_list("people",q,school,page,limit,user_id)


@router.get("/clubs")
async def list_clubs(q: str = Query(default="",max_length=200),school: str = Query(default="",max_length=120),page: int = Query(default=1,ge=1),limit: int = Query(default=30,ge=1,le=100),user_id: str = Depends(auth.require_user)):
    return await _entity_list("clubs",q,school,page,limit,user_id)


@router.get("/events")
async def list_events(q: str = Query(default="",max_length=200),school: str = Query(default="",max_length=120),page: int = Query(default=1,ge=1),limit: int = Query(default=30,ge=1,le=100),user_id: str = Depends(auth.require_user)):
    return await _entity_list("events",q,school,page,limit,user_id)


@router.get("/scenes")
async def list_scenes(q: str = Query(default="",max_length=200),page: int = Query(default=1,ge=1),limit: int = Query(default=30,ge=1,le=100),user_id: str = Depends(auth.require_user)):
    return await _entity_list("scenes",q,"",page,limit,user_id)


@router.get("/learning/insights")
async def learning_insights(user_id: str = Depends(auth.require_user)) -> dict[str,Any]:
    return get_visual_store().learning_insights(user_id)


@router.get("/learning/corrections")
async def learning_corrections(page: int = Query(default=1,ge=1),limit: int = Query(default=30,ge=1,le=100),user_id: str = Depends(auth.require_user)) -> dict[str,Any]:
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


@router.get("/visual-backend/status")
async def visual_backend_status(user_id: str = Depends(auth.require_user)) -> dict[str,Any]:
    """Return external dependency state without exposing credentials."""
    del user_id
    return {"backend": "insforge", "status": get_insforge_adapters().database.health().as_dict(), "local_source_of_truth": True}


@router.post("/visual-sync/run", status_code=202)
async def run_visual_sync(req: VisualSyncRequest, request: Request, user_id: str = Depends(auth.require_user)) -> dict[str,Any]:
    """Synchronize metadata (and trusted non-private files) to InsForge.

    This is deliberately synchronous per batch but durable: every item is
    committed independently, so a failed remote request never loses local data.
    """
    try:
        result = await run_in_threadpool(
            InsForgeSyncAdapter(get_visual_store()).run, user_id,
            asset_ids=req.asset_ids, project_id=req.project_id,
            idempotency_key=req.idempotency_key, resumed_from=req.resumed_from,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    audit.write_audit(action="visual.sync", actor_user_id=user_id, resource=result.get("id", ""), detail={"status": result.get("status"), "failed": result.get("failed_count", 0)}, request=request, ok=result.get("status") != "failed")
    return {"backend": "insforge", "local_source_of_truth": True, **result}


@router.get("/visual-sync/{run_id}")
async def get_visual_sync(run_id: str, user_id: str = Depends(auth.require_user)) -> dict[str,Any]:
    result = InsForgeSyncAdapter(get_visual_store()).get_run(run_id, user_id)
    if not result:
        raise HTTPException(status_code=404, detail="找不到同步紀錄")
    return result


@router.post("/visual-sync/{run_id}/retry", status_code=202)
async def retry_visual_sync(run_id: str, req: VisualSyncRetryRequest, request: Request, user_id: str = Depends(auth.require_user)) -> dict[str,Any]:
    result = await run_in_threadpool(InsForgeSyncAdapter(get_visual_store()).retry,run_id,user_id,req.asset_ids)
    if not result:
        raise HTTPException(status_code=404, detail="找不到同步紀錄")
    audit.write_audit(action="visual.sync_retry", actor_user_id=user_id, resource=run_id, detail={"new_run_id": result.get("id")}, request=request)
    return result


@router.post("/visual-sync/{run_id}/rollback")
async def rollback_visual_sync(run_id: str, request: Request, user_id: str = Depends(auth.require_user)) -> dict[str,Any]:
    result = InsForgeSyncAdapter(get_visual_store()).rollback(run_id, user_id)
    if not result:
        raise HTTPException(status_code=404, detail="找不到同步紀錄")
    audit.write_audit(action="visual.sync_rollback", actor_user_id=user_id, resource=run_id, detail={"remote_delete": False}, request=request)
    return {"warning": "僅標記本地同步參照已回滾，未刪除原始檔或遠端資料", **result}


@router.post("/backend-sync/run", status_code=202)
async def run_backend_sync(req: BackendSyncRequest, request: Request, user_id: str = Depends(auth.require_user)) -> dict[str,Any]:
    service = InsForgeDataSyncAdapter()
    groups = set(req.resource_types)
    try:
        if req.dry_run:
            return await run_in_threadpool(service.preview,user_id,groups=groups,project_id=req.project_id)
        result = await run_in_threadpool(
            service.run, user_id, groups=groups, project_id=req.project_id,
            idempotency_key=req.idempotency_key,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422,detail={"code":"invalid_sync_scope","message":str(exc)}) from exc
    audit.write_audit(
        action="backend.sync", actor_user_id=user_id, resource=result.get("id", ""),
        detail={"groups":sorted(groups),"status":result.get("status"),"failed":result.get("failed_count",0)},
        request=request,ok=result.get("status") in {"completed","partial"},
    )
    return {"backend":"insforge","local_source_of_truth":True,**result}


@router.get("/backend-sync/{run_id}")
async def get_backend_sync(run_id: str, user_id: str = Depends(auth.require_user)) -> dict[str,Any]:
    result = InsForgeDataSyncAdapter().get_run(run_id,user_id)
    if not result:
        raise HTTPException(status_code=404,detail="找不到核心資料同步紀錄")
    return result


@router.post("/backend-sync/{run_id}/retry", status_code=202)
async def retry_backend_sync(run_id: str, request: Request, user_id: str = Depends(auth.require_user)) -> dict[str,Any]:
    result = await run_in_threadpool(InsForgeDataSyncAdapter().retry,run_id,user_id)
    if not result:
        raise HTTPException(status_code=404,detail="找不到核心資料同步紀錄")
    audit.write_audit(action="backend.sync_retry",actor_user_id=user_id,resource=run_id,detail={"new_run_id":result.get("id")},request=request)
    return result


@router.post("/backend-sync/{run_id}/rollback")
async def rollback_backend_sync(run_id: str, request: Request, user_id: str = Depends(auth.require_user)) -> dict[str,Any]:
    result = InsForgeDataSyncAdapter().rollback(run_id,user_id)
    if not result:
        raise HTTPException(status_code=404,detail="找不到核心資料同步紀錄")
    audit.write_audit(action="backend.sync_rollback",actor_user_id=user_id,resource=run_id,detail={"remote_delete":False},request=request)
    return {"warning":"只回滾本地同步參照；不刪除本機或 InsForge 原始資料",**result}


@router.get("/data-organization/inventory")
async def get_data_inventory(refresh: bool = Query(default=False), project_id: str = Query(default="",max_length=160), user_id: str = Depends(auth.require_user)) -> dict[str,Any]:
    service = DataOrganizationService()
    if not refresh:
        existing = service.latest_inventory(user_id,project_id)
        if existing:
            return existing
    try:
        return await run_in_threadpool(service.inventory,user_id,project_id=project_id,persist=True)
    except ValueError as exc:
        raise HTTPException(status_code=422,detail=str(exc)) from exc


@router.post("/data-organization/inventory",status_code=201)
async def run_data_inventory(req: InventoryRequest, request: Request, user_id: str = Depends(auth.require_user)) -> dict[str,Any]:
    try:
        result = await run_in_threadpool(DataOrganizationService().inventory,user_id,project_id=req.project_id,persist=True)
    except ValueError as exc:
        raise HTTPException(status_code=422,detail=str(exc)) from exc
    audit.write_audit(action="data.inventory",actor_user_id=user_id,resource=result["id"],detail={"statistics":result["statistics"]},request=request)
    return result


@router.post("/data-organization/organize",status_code=202)
async def organize_existing_data(req: OrganizeRequest, request: Request, user_id: str = Depends(auth.require_user)) -> dict[str,Any]:
    try:
        result = await run_in_threadpool(DataOrganizationService().run,user_id,idempotency_key=req.idempotency_key,project_id=req.project_id)
    except ValueError as exc:
        raise HTTPException(status_code=422,detail=str(exc)) from exc
    audit.write_audit(action="data.organize",actor_user_id=user_id,resource=result.get("id", ""),detail={"status":result.get("status"),"failed":result.get("failed_count",0)},request=request,ok=result.get("status") in {"completed","partial"})
    return result


@router.get("/data-organization/runs/{run_id}")
async def get_data_organization_run(run_id: str,user_id: str = Depends(auth.require_user)) -> dict[str,Any]:
    result = DataOrganizationService().get_run(run_id,user_id)
    if not result:
        raise HTTPException(status_code=404,detail="找不到資料整理同步紀錄")
    return result


@router.post("/data-organization/runs/{run_id}/retry",status_code=202)
async def retry_data_organization_run(run_id: str,request: Request,user_id: str = Depends(auth.require_user)) -> dict[str,Any]:
    result = await run_in_threadpool(DataOrganizationService().retry,run_id,user_id)
    if not result:
        raise HTTPException(status_code=404,detail="找不到資料整理同步紀錄")
    audit.write_audit(action="data.organize_retry",actor_user_id=user_id,resource=run_id,detail={"new_run_id":result.get("id","")},request=request)
    return result


@router.get("/data-organization/queue")
async def data_organization_queue(category: str = Query(default="pending_review",max_length=30),page: int = Query(default=1,ge=1),limit: int = Query(default=30,ge=1,le=100),user_id: str = Depends(auth.require_user)) -> dict[str,Any]:
    capped = min(limit,config.VISUAL_SEARCH_LIMIT)
    items,total = DataOrganizationService().list_queue(user_id,category=category,page=page,limit=capped)
    return {"items":items,"total":total,"page":page,"limit":capped,"category":category}


@router.post("/library/context-nodes",status_code=201)
async def save_library_context_node(req: ContextNodeRequest,request: Request,user_id: str = Depends(auth.require_user)) -> dict[str,Any]:
    try:
        result = LibraryContextResolver().upsert_node(user_id,project_id=req.project_id,node_type=req.node_type,title=req.title,position=req.position,parent_id=req.parent_id,requirements=req.requirements)
    except ValueError as exc:
        raise HTTPException(status_code=422,detail=str(exc)) from exc
    audit.write_audit(action="library.context_save",actor_user_id=user_id,resource=result.get("id", ""),detail={"project_id":req.project_id,"node_type":req.node_type},request=request)
    return result


@router.post("/library/context-search")
async def search_library_context(req: ContextSearchRequest,user_id: str = Depends(auth.require_user)) -> dict[str,Any]:
    try:
        return await run_in_threadpool(LibraryContextResolver().search,user_id,query=req.query,project_id=req.project_id,scene_position=req.scene_position,shot_position=req.shot_position,page=req.page,limit=req.limit)
    except ValueError as exc:
        raise HTTPException(status_code=422,detail=str(exc)) from exc
