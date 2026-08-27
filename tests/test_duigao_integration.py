"""duigao Room Context boundary tests.

These tests exercise the real FastAPI route and the provider adapter without a
network call to NVIDIA or fal.ai.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time

import pytest
from fastapi.testclient import TestClient

from app import config
from app.main import app
from app.services import asset_providers, duigao_integration, fal as fal_service


def signed(body: str, secret: str, timestamp: int | None = None) -> dict[str, str]:
    ts = str(timestamp if timestamp is not None else int(time.time()))
    digest = hmac.new(secret.encode(), f"{ts}.{body}".encode(), hashlib.sha256).hexdigest()
    return {"x-duigao-timestamp": ts, "x-duigao-signature": f"sha256={digest}"}


@pytest.fixture()
def integration_client(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config, "DUIGAO_CONTEXT_SHARED_SECRET", "test-duigao-secret-which-is-long-enough")
    with TestClient(app) as client:
        yield client


def context_body() -> str:
    return json.dumps({
        "query": "目前最新版是哪一版？",
        "context": [{
            "sourceId": "asset-1", "assetId": "asset-1", "title": "擺攤文宣改二", "assetType": "image",
            "versionLabel": "改二", "isCurrent": True, "archived": False,
            "summary": "主標題與 QR Code 已建立索引", "topics": ["擺攤"], "keywords": ["QR"],
        }],
        "sources": [{"sourceId": "asset-1", "assetId": "asset-1", "title": "擺攤文宣改二"}],
        "relations": [],
    }, ensure_ascii=False, separators=(",", ":"))


def test_invalid_signature_is_rejected(integration_client):
    body = context_body()
    response = integration_client.post("/api/v1/room-context/answer", content=body, headers={"content-type": "application/json", "x-duigao-timestamp": str(int(time.time())), "x-duigao-signature": "sha256=" + "0" * 64})
    assert response.status_code == 401


def test_stale_signature_is_rejected(integration_client):
    body = context_body()
    headers = signed(body, config.DUIGAO_CONTEXT_SHARED_SECRET, int(time.time()) - 301)
    response = integration_client.post("/api/v1/room-context/answer", content=body, headers={**headers, "content-type": "application/json"})
    assert response.status_code == 401


def test_valid_signed_route_does_not_need_human_cookie(integration_client, monkeypatch):
    async def fake_answer(req):
        return duigao_integration.DuigaoAnswer(text="最新版是改二。", citations=[{"sourceId": "asset-1"}], actions=[], model="test")

    monkeypatch.setattr(duigao_integration, "answer_room_context", fake_answer)
    body = context_body()
    response = integration_client.post("/api/v1/room-context/answer", content=body, headers={**signed(body, config.DUIGAO_CONTEXT_SHARED_SECRET), "content-type": "application/json"})
    assert response.status_code == 200
    assert response.json()["text"] == "最新版是改二。"


def test_context_prompt_has_no_storage_or_invite_boundary():
    req = duigao_integration.DuigaoContextRequest.model_validate(json.loads(context_body()))
    prompt = duigao_integration._context_prompt(req)
    assert "storage_path" not in prompt
    assert "invite" not in prompt.lower()
    assert "asset-1" in prompt


def test_whiteboard_graph_is_available_to_the_agent_prompt():
    req = duigao_integration.DuigaoContextRequest.model_validate({
        "query": "幫我整理這張白板",
        "context": [{
            "sourceId": "whiteboard-1", "assetId": "whiteboard-1", "title": "擺攤流程", "assetType": "whiteboard",
            "isCurrent": True, "archived": False, "topics": ["招生"], "keywords": [],
            "structuredData": {"nodes": [{"id": "node-1", "content": "招生"}], "edges": [{"from": "node-1", "to": "node-2"}]},
        }],
        "sources": [{"sourceId": "whiteboard-1", "assetId": "whiteboard-1", "title": "擺攤流程"}],
        "relations": [],
    })
    assert '"node-1"' in duigao_integration._context_prompt(req)


def test_answer_discards_unknown_citations():
    class FakeReply:
        content = json.dumps({"answer": "有證據。", "citations": [{"sourceId": "asset-1"}, {"sourceId": "not-in-context"}]})

    class FakeClient:
        model = "fake-nim"

        async def chat(self, *_args, **_kwargs):
            return FakeReply()

    req = duigao_integration.DuigaoContextRequest.model_validate(json.loads(context_body()))
    result = __import__("asyncio").run(duigao_integration.answer_room_context(req, FakeClient()))
    assert [item.sourceId for item in result.citations] == ["asset-1"]
    assert result.provider == "tku-zen-agent"


def test_provider_policy_and_embedding_fallback_are_explicit(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config, "DUIGAO_ANALYSIS_PROVIDER_EXTERNAL", False)
    policy = asset_providers.approved_provider_policy()
    assert policy.supports("image")
    assert policy.supports("document")
    assert not policy.supports("other")
    assert policy.is_external is False
    assert policy.allowed_for_private_assets is True


@pytest.mark.asyncio
async def test_disabled_embedding_provider_is_a_safe_noop():
    provider = asset_providers.DisabledEmbeddingProvider()
    assert provider.enabled is False
    assert await provider.embed(["擺攤文宣", "招生影片"]) == []


@pytest.mark.asyncio
async def test_asset_analysis_honours_external_policy():
    request = duigao_integration.DuigaoAssetAnalysisRequest(assetId="asset-1", assetType="image", title="海報", externalAiAllowed=False)
    result = await duigao_integration.analyze_asset(request)
    assert result.errorCode == "EXTERNAL_AI_BLOCKED"


@pytest.mark.asyncio
async def test_image_analysis_returns_normalized_regions_and_structured_facts(monkeypatch: pytest.MonkeyPatch):
    async def fake_vision(_attachments, *, prompt):
        assert "headline" in prompt
        return json.dumps({
            "summary": "校園活動文宣，適合招生宣傳。",
            "structuredData": {"headline": "加入我們", "location": "海報街", "qrCode": {"present": True}},
            "regions": [{"type": "headline", "label": "主標題", "text": "加入我們", "x": 0.1, "y": 0.08, "width": 0.8, "height": 0.16, "confidence": 0.94}],
        })

    monkeypatch.setattr(fal_service, "analyze_images", fake_vision)
    request = duigao_integration.DuigaoAssetAnalysisRequest(
        assetId="poster-1", assetType="image", title="擺攤文宣改二", sourceUrl="https://signed.example/poster.jpg", externalAiAllowed=True,
    )
    result = await duigao_integration.analyze_asset(request)
    assert result.errorCode is None
    assert result.regions[0].x == 0.1
    assert result.structuredData["headline"] == "加入我們"


@pytest.mark.asyncio
async def test_document_analysis_extracts_text_without_an_external_model():
    request = duigao_integration.DuigaoAssetAnalysisRequest(
        assetId="doc-1",
        assetType="document",
        title="擺攤計畫.md",
        mimeType="text/markdown",
        textContent="# 擺攤計畫\n\n□ 準備 QR Code\n地點：海報街",
        externalAiAllowed=False,
    )
    result = await duigao_integration.analyze_asset(request)
    assert result.errorCode is None
    assert "QR Code" in result.detectedText
    assert result.documentChunks[0].page is None
    assert result.documentChunks[0].startOffset == 0


@pytest.mark.asyncio
async def test_video_frame_fallback_is_not_reported_as_a_timeline(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config, "FAL_VIDEO_ENDPOINT", "")

    async def fake_vision(_attachments, *, prompt):
        assert "整支影片" in prompt
        return json.dumps({"summary": "取樣畫面有校園活動", "topics": ["校園"]})

    monkeypatch.setattr(fal_service, "analyze_images", fake_vision)
    request = duigao_integration.DuigaoAssetAnalysisRequest(
        assetId="video-1",
        assetType="video",
        title="招生影片",
        sourceUrl="https://signed.example/video.mp4",
        keyframes=[{"imageUrl": "https://signed.example/frame.jpg", "startSeconds": 42, "endSeconds": 55}],
        externalAiAllowed=True,
    )
    result = await duigao_integration.analyze_asset(request)
    assert result.errorCode == "PROVIDER_UNAVAILABLE"
    assert result.segments == []
    assert result.structuredData["timelineAvailable"] is False
