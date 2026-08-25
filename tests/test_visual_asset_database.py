"""視覺資料庫端到端契約：使用合成圖片與離線 Vision fake，不消耗外部額度。"""

from __future__ import annotations

import io
import tempfile
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from app import config, main
from app.services import visual_assets


@pytest.fixture()
def visual_env(tmp_db, monkeypatch):
    root = Path(tempfile.mkdtemp(prefix="tku_visual_"))
    monkeypatch.setattr(config,"VISUAL_ASSET_DIR",root / "assets")
    monkeypatch.setattr(config,"VISUAL_EXPORT_DIR",root / "exports")
    monkeypatch.setattr(config,"VISUAL_MAX_FILE_BYTES",2_000_000)
    monkeypatch.setattr(config,"VISUAL_MAX_BATCH",30)
    monkeypatch.setattr(config,"VISUAL_SEARCH_LIMIT",60)
    if visual_assets._visual_store is not None:
        visual_assets._visual_store.close()
        visual_assets._visual_store = None
    yield root
    if visual_assets._visual_store is not None:
        visual_assets._visual_store.close()
        visual_assets._visual_store = None
    import shutil
    shutil.rmtree(root,ignore_errors=True)


def picture(*, color=(30,120,180), size=(900,600), poster=False) -> bytes:
    image = Image.new("RGB",size,color)
    draw = ImageDraw.Draw(image)
    if poster:
        draw.rectangle((40,40,size[0]-40,size[1]-40),outline=(255,255,255),width=8)
        draw.text((90,120),"ZEN EVENT 2025-09-18",fill=(255,255,255))
        for x in range(50,size[0]-50,35):
            draw.line((x,300,x+20,430),fill=(240,210,50),width=4)
    out = io.BytesIO()
    image.save(out,format="PNG")
    return out.getvalue()


def upload(client: TestClient, content: bytes, *, name="photo.png", school="淡江大學", club="領袖禪學社", auto=False):
    return client.post(
        "/api/visual-assets/upload",
        files=[("files",(name,content,"image/png"))],
        data={"school":school,"club":club,"privacy":"private","commercial_use":"unknown","auto_analyze":str(auto).lower()},
    )


def test_upload_preserves_original_creates_thumbnail_quality_hash_and_duplicate(visual_env):
    content = picture(poster=True)
    with TestClient(main.app) as client:
        first = upload(client,content)
        second = upload(client,content,name="copy.png")
        assert first.status_code == 201
        assert second.status_code == 201
        a = first.json()["items"][0]
        b = second.json()["items"][0]
        assert a["width"] == 900 and a["height"] == 600
        assert a["quality_score"] > 0
        assert b["duplicate_of"] == a["asset_id"]
        original = client.get(a["original_url"])
        thumb = client.get(a["thumbnail_url"])
        assert original.content == content
        assert thumb.status_code == 200 and thumb.headers["content-type"].startswith("image/jpeg")


def test_poster_analysis_ocr_date_activity_scene_and_conflict(visual_env,monkeypatch):
    from app.services import visual_analysis

    async def fake_analysis(_url):
        return {
            "summary":"淡江領袖禪學社期初茶會海報",
            "ocr_text":"期初茶會\n2025/09/18 18:30\n淡江大學",
            "dates":[{"value":"2025/09/18","confidence":.97,"evidence":"海報日期"}],
            "scenes":[{"label":"海報","confidence":.99,"evidence":"文字版面"}],
            "event":{"name":"期初茶會","type":"茶會","location":"淡江大學","start_time":"18:30","end_time":"20:30","confidence":.94,"evidence":"主標題"},
            "clubs":[{"name":"領袖禪學社","school":"淡江大學","confidence":.9,"evidence":"Logo文字"}],
            "people":{"count":0,"descriptions":[]},"objects":["海報"],"logos":[],"quality_notes":[],"is_poster":True,
        }

    async def no_people(_target,_refs):
        return []

    monkeypatch.setattr(visual_analysis.fal,"analyze_visual_asset",fake_analysis)
    monkeypatch.setattr(visual_analysis.fal,"compare_confirmed_people",no_people)
    with TestClient(main.app) as client:
        asset = upload(client,picture(poster=True)).json()["items"][0]
        analyzed = client.post("/api/visual-assets/analyze",json={"asset_ids":[asset["asset_id"]]})
        assert analyzed.status_code == 200 and analyzed.json()["completed"] == 1
        item = analyzed.json()["items"][0]
        assert "期初茶會" in item["ocr_text"]
        assert any(d["value"] == "2025-09-18" and d["source"] == "ocr" for d in item["date_candidates"])
        assert any(o["entity_type"] == "event" and o["status"] == "possible" for o in item["observations"])
        event = client.get("/api/events?q=期初茶會").json()["items"][0]
        assert event["start_time"] == "18:30" and event["end_time"] == "20:30"
        # 人工輸入不同日期不會覆寫 OCR；兩個候選並列並標示衝突。
        fixed = client.post("/api/entities/confirm",json={
            "asset_id":asset["asset_id"],"entity_type":"date","action":"correct","candidate_label":"2025-09-19",
            "values":{},"reason":"活動承辦人確認",
        })
        assert fixed.status_code == 200
        assert fixed.json()["date_status"] == "conflict"
        assert {d["value"] for d in fixed.json()["date_candidates"]} >= {"2025-09-18","2025-09-19"}


def test_same_name_people_are_distinct_and_cross_school_link_is_blocked(visual_env):
    with TestClient(main.app) as client:
        tku = upload(client,picture(color=(20,80,160)),name="tku.png",school="淡江大學").json()["items"][0]
        nccu = upload(client,picture(color=(120,40,60)),name="nccu.png",school="政治大學",club="領袖社").json()["items"][0]
        tku_person = client.post("/api/entities/confirm",json={
            "asset_id":tku["asset_id"],"entity_type":"person","action":"correct","candidate_label":"陳同學",
            "values":{"name":"陳同學","school":"淡江大學","club":"領袖禪學社"},"reason":"本人確認",
        })
        nccu_person = client.post("/api/entities/confirm",json={
            "asset_id":nccu["asset_id"],"entity_type":"person","action":"correct","candidate_label":"陳同學",
            "values":{"name":"陳同學","school":"國立政治大學","club":"領袖社"},"reason":"本人確認",
        })
        assert tku_person.status_code == nccu_person.status_code == 200
        people = client.get("/api/people?q=陳同學").json()
        assert people["total"] == 2
        school_search = client.get("/api/visual-assets/search",params={"q":"淡江 陳同學","person":"陳同學"}).json()
        assert school_search["total"] == 1
        assert school_search["items"][0]["asset_id"] == tku["asset_id"]
        tku_id = next(p["id"] for p in people["items"] if p["school_id"] == tku_person.json()["school_id"])
        blocked = client.post("/api/entities/confirm",json={
            "asset_id":nccu["asset_id"],"entity_type":"person","entity_id":tku_id,"action":"confirm","candidate_label":"陳同學","values":{},
        })
        assert blocked.status_code == 409
        assert blocked.json()["detail"]["code"] == "school_conflict"


def test_natural_language_and_search_by_image_rank_real_assets(visual_env):
    with TestClient(main.app) as client:
        target = upload(client,picture(color=(20,150,190),poster=True),name="招生校園.png").json()["items"][0]
        upload(client,picture(color=(180,40,30)),name="聚餐.png")
        confirmed = client.post("/api/entities/confirm",json={
            "asset_id":target["asset_id"],"entity_type":"scene","action":"confirm","candidate_label":"校園","values":{},"reason":"使用者確認",
        })
        assert confirmed.status_code == 200
        text_search = client.get("/api/visual-assets/search",params={"q":"淡江 校園 招生","scene":"校園"}).json()
        assert text_search["total"] >= 1
        assert text_search["items"][0]["asset_id"] == target["asset_id"]
        image_search = client.post(
            "/api/visual-assets/search-by-image",
            files={"file":("query.png",picture(color=(20,150,190),poster=True),"image/png")},
        )
        assert image_search.status_code == 200
        assert image_search.json()["items"][0]["asset_id"] == target["asset_id"]
        assert image_search.json()["items"][0]["match_score"] > .9


def test_person_comparison_uses_only_confirmed_reference_and_stays_possible(visual_env,monkeypatch):
    from app.services import visual_analysis

    person_id = ""

    async def fake_analysis(_url):
        return {
            "summary":"一位人物在教室","ocr_text":"","dates":[],
            "scenes":[{"label":"教室","confidence":.9,"evidence":"白板與課桌"}],
            "event":{},"clubs":[],"people":{"count":1,"descriptions":[{"label":"人物1","confidence":.8,"evidence":"正面臉部清楚"}]},
            "objects":["課桌"],"logos":[],"quality_notes":[],"is_poster":False,
        }

    async def confirmed_match(_target,references):
        assert len(references) == 1
        assert references[0]["person_id"] == person_id
        return [{"person_id":person_id,"confidence":.86,"evidence":"與人工確認參考圖相似"}]

    monkeypatch.setattr(visual_analysis.fal,"analyze_visual_asset",fake_analysis)
    monkeypatch.setattr(visual_analysis.fal,"compare_confirmed_people",confirmed_match)
    with TestClient(main.app) as client:
        reference = upload(client,picture(color=(60,100,140)),name="reference.png").json()["items"][0]
        confirmed = client.post("/api/entities/confirm",json={
            "asset_id":reference["asset_id"],"entity_type":"person","action":"correct","candidate_label":"薰儀",
            "values":{"name":"薰儀","school":"淡江大學","use_as_reference":True},"reason":"本人確認",
        }).json()
        person_id = next(o["entity_id"] for o in confirmed["observations"] if o["entity_type"] == "person")
        target = upload(client,picture(color=(62,102,142)),name="target.png").json()["items"][0]
        result = client.post("/api/visual-assets/analyze",json={"asset_ids":[target["asset_id"]]}).json()["items"][0]
        candidate = next(o for o in result["observations"] if o["entity_type"] == "person" and o["entity_id"] == person_id)
        assert candidate["label"] == "可能是薰儀"
        assert candidate["status"] == "possible"
        assert candidate["evidence"]["requires_user_confirmation"] is True


def test_confirm_correction_learning_export_and_immutable_original(visual_env):
    with TestClient(main.app) as client:
        asset = upload(client,picture(poster=True)).json()["items"][0]
        corrected = client.post("/api/entities/confirm",json={
            "asset_id":asset["asset_id"],"entity_type":"event","action":"correct","candidate_label":"115 上學期期初茶會",
            "values":{"name":"115 上學期期初茶會","school":"淡江大學","date":"2026-09-18","location":"商管大樓"},"reason":"活動負責人修正",
        })
        assert corrected.status_code == 200
        assert any(o["label"] == "115 上學期期初茶會" and o["status"] == "confirmed" for o in corrected.json()["observations"])
        assert client.post(f"/api/visual-assets/{asset['asset_id']}/usage",json={"action":"selected","context":{"use":"Instagram"}}).status_code == 200
        storyboard = client.post(f"/api/visual-assets/{asset['asset_id']}/usage",json={"action":"storyboard","context":{"note":"開場畫面","rendition":"16:9"}})
        assert storyboard.status_code == 200
        assert storyboard.json()["collection"]["kind"] == "storyboard"
        collections = client.get("/api/visual-collections?kind=storyboard").json()
        assert collections["total"] == 1 and collections["items"][0]["item_count"] == 1
        insights = client.get("/api/learning/insights").json()
        assert any(row["event_type"] == "correction" for row in insights["event_counts"])
        corrections = client.get("/api/learning/corrections").json()
        assert corrections["total"] >= 1
        immutable = client.patch(f"/api/visual-assets/{asset['asset_id']}",json={"storage_path":"overwrite.png"})
        assert immutable.status_code == 422
        exported = client.post("/api/visual-assets/export",json={"asset_ids":[asset["asset_id"]],"rendition":"1:1"})
        assert exported.status_code == 200
        with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
            assert "manifest.json" in archive.namelist()
            assert "manifest.csv" in archive.namelist()
            image_name = next(name for name in archive.namelist() if name.startswith("images/"))
            with Image.open(io.BytesIO(archive.read(image_name))) as rendition:
                assert rendition.width == rendition.height


def test_auth_invalid_oversize_and_broken_images(visual_env,monkeypatch):
    monkeypatch.setattr(config,"AUTH_MODE","token")
    monkeypatch.setattr(config,"APP_ACCESS_TOKEN","secret-code")
    with TestClient(main.app,base_url="https://testserver") as client:
        denied = upload(client,picture())
        assert denied.status_code == 401
        assert client.post("/api/auth",json={"token":"secret-code"}).status_code == 200
        monkeypatch.setattr(config,"VISUAL_MAX_FILE_BYTES",100)
        too_large = upload(client,b"x" * 101)
        assert too_large.status_code == 413
        monkeypatch.setattr(config,"VISUAL_MAX_FILE_BYTES",2_000_000)
        broken = upload(client,b"not an image")
        assert broken.status_code == 422
        assert broken.json()["errors"][0]["code"] == "invalid_image"


def test_mobile_visual_workspace_contract(project_root):
    html = (project_root / "app/static/visual-assets.html").read_text(encoding="utf-8")
    js = (project_root / "app/static/visual-assets.js").read_text(encoding="utf-8")
    css = (project_root / "app/static/visual-assets.css").read_text(encoding="utf-8")
    for text in ("multiple", "拖曳圖片", "paste", "clipboardData", "以圖搜圖", "分析進度", "加入貼文", "加入分鏡"):
        assert text in html or text in js
    for ratio in ("1:1","4:5","9:16","16:9"):
        assert ratio in html and ratio in js
    assert "@media (max-width: 560px)" in css
    assert "min-height: 44px" in css or "style.css" in html
    assert "localStorage" not in js and "sessionStorage" not in js and "document.cookie" not in js
