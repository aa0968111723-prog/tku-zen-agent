"use strict";

const $ = (id) => document.getElementById(id);
const visualState = { panel: "dashboard", uploadFiles: [], uploadMode: "batch", selected: new Set(), lastItems: [], pollers: new Map(), organizationRun: null };

function node(tag, cls, text) {
  const item = document.createElement(tag);
  if (cls) item.className = cls;
  if (text !== undefined) item.textContent = text;
  return item;
}

function setStatus(text, tone = "cyan") {
  const host = $("visual-status");
  host.textContent = text || "";
  host.style.color = `var(--neon-${tone})`;
}

async function detailFromResponse(resp) {
  try {
    const data = await resp.clone().json();
    if (typeof data.detail === "string") return data.detail;
    if (data.detail && data.detail.message) return data.detail.message;
    if (Array.isArray(data.detail)) return data.detail.map((d) => d.msg || String(d)).join("；");
  } catch { /* non-json */ }
  return "系統暫時無法完成操作";
}

async function requestJSON(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (options.body && typeof options.body === "string") headers["Content-Type"] = "application/json";
  const resp = await fetch(path, { ...options, headers });
  if (resp.status === 401) {
    showGate();
    throw new Error("請先輸入授權碼");
  }
  if (!resp.ok) throw new Error(await detailFromResponse(resp));
  return resp.json();
}

function showGate(message = "") {
  $("visual-app").hidden = true;
  $("visual-gate").hidden = false;
  $("visual-gate-error").textContent = message;
  $("visual-gate-error").hidden = !message;
  $("visual-token").focus();
}

function showPanel(name) {
  visualState.panel = name;
  document.querySelectorAll(".visual-panel").forEach((panel) => { panel.hidden = panel.id !== `panel-${name}`; });
  document.querySelectorAll(".visual-tabs button").forEach((button) => {
    button.classList.toggle("is-active", button.dataset.panel === name);
  });
  window.scrollTo({ top: 0, behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
  if (name === "dashboard") loadDashboard();
  if (name === "review") loadReview("pending_review");
  if (name === "organize") loadOrganization();
}

function statusLabel(value) {
  return ({ verified: "已確認", complete: "分析完成", probable: "可能", pending_review: "待確認", conflicted: "衝突", failed: "分析失敗", analyzing: "分析中", processing_entities: "建立關聯中", local_complete: "本機檢查完成", needs_vision_config: "AI 分析未啟用", analysis_failed: "分析失敗" })[value] || value || "待確認";
}

function badge(value, label = "") {
  return node("span", `status-badge ${value || "pending_review"}`, label || statusLabel(value));
}

function emptyCard(text) {
  return node("p", "empty-visual", text);
}

function assetCard(asset, { selectable = false, confirmable = false, retryable = false } = {}) {
  const card = node("article", "asset-card");
  card.dataset.assetId = asset.asset_id;
  if (visualState.selected.has(asset.asset_id)) card.classList.add("is-selected");
  const thumb = node("div", "asset-thumb");
  const image = document.createElement("img");
  image.src = asset.thumbnail_url;
  image.alt = asset.original_filename || "圖片縮圖";
  image.loading = "lazy";
  thumb.appendChild(image);
  if (selectable) {
    const select = node("button", "select-asset", visualState.selected.has(asset.asset_id) ? "✓" : "＋");
    select.type = "button";
    select.setAttribute("aria-label", "加入或移出素材包");
    select.addEventListener("click", () => toggleSelection(asset, card, select));
    thumb.appendChild(select);
  }
  card.appendChild(thumb);
  const body = node("div", "asset-body");
  body.appendChild(node("h3", "asset-title", asset.original_filename || "未命名圖片"));
  body.appendChild(node("p", "asset-meta", `${asset.width || 0}×${asset.height || 0} · 畫質 ${Math.round(asset.quality_score || 0)}${asset.school_name ? ` · ${asset.school_name}` : ""}`));
  const badges = node("div", "badge-row");
  badges.appendChild(badge(asset.analysis_status));
  if (asset.review_status) badges.appendChild(badge(asset.review_status));
  if (asset.duplicate_of) badges.appendChild(badge("conflicted", "重複素材"));
  if (asset.commercial_use === "allowed") badges.appendChild(badge("verified", "可商用"));
  if (asset.commercial_use === "unknown") badges.appendChild(badge("probable", "權限待確認"));
  body.appendChild(badges);
  if (asset.recommendation_reasons && asset.recommendation_reasons.length) {
    const list = node("ul", "asset-reasons");
    asset.recommendation_reasons.slice(0, 3).forEach((reason) => list.appendChild(node("li", null, reason)));
    body.appendChild(list);
  }
  const actions = node("div", "asset-actions");
  const inspect = node("button", null, "查看分析");
  inspect.type = "button";
  inspect.addEventListener("click", () => openAsset(asset.asset_id));
  const download = node("a", null, "下載");
  download.href = `/api/visual-assets/${encodeURIComponent(asset.asset_id)}/file?variant=original&download=true`;
  download.addEventListener("click", () => recordUsage(asset.asset_id, "downloaded", { source: "asset_card" }));
  actions.append(inspect, download);
  if (confirmable && asset.review_status !== "verified") {
    const confirm = node("button", "primary", "一鍵確認"); confirm.type = "button";
    confirm.addEventListener("click", async () => {
      try { await requestJSON(`/api/visual-assets/${encodeURIComponent(asset.asset_id)}/confirm`, { method: "POST", body: JSON.stringify({ reason: "使用者在待確認清單一鍵確認" }) }); setStatus("素材已確認", "green"); loadReview("pending_review"); }
      catch (error) { setStatus(error.message, "red"); }
    });
    actions.appendChild(confirm);
  }
  if (retryable) {
    const retry = node("button", null, "重試分析"); retry.type = "button";
    retry.addEventListener("click", async () => {
      setStatus("正在重試這個素材…");
      try { await requestJSON(`/api/visual-assets/${encodeURIComponent(asset.asset_id)}/retry`, { method: "POST" }); setStatus("重試完成", "green"); }
      catch (error) { setStatus(error.message, "red"); }
      loadReview("failed");
    });
    actions.appendChild(retry);
  }
  if (selectable) {
    const storyboard = node("button", null, "加入分鏡");
    storyboard.type = "button";
    storyboard.addEventListener("click", async () => { await recordUsage(asset.asset_id, "storyboard", { source: "search" }); setStatus("已記錄為影片分鏡素材", "green"); });
    const social = node("button", null, "加入貼文");
    social.type = "button";
    social.addEventListener("click", async () => { await recordUsage(asset.asset_id, "social_post", { source: "search" }); setStatus("已加入目前社群貼文素材集合", "green"); });
    actions.append(storyboard, social);
  }
  body.appendChild(actions);
  card.appendChild(body);
  return card;
}

function renderAssetGrid(host, items, options = {}) {
  host.replaceChildren();
  if (!items.length) { host.appendChild(emptyCard(options.empty || "目前沒有符合條件的圖片")); return; }
  items.forEach((asset) => host.appendChild(assetCard(asset, options)));
}

function toggleSelection(asset, card, button) {
  if (visualState.selected.has(asset.asset_id)) {
    visualState.selected.delete(asset.asset_id);
    card.classList.remove("is-selected");
    button.textContent = "＋";
    recordUsage(asset.asset_id, "excluded", { reason: "removed_from_material_pack" });
  } else {
    visualState.selected.add(asset.asset_id);
    card.classList.add("is-selected");
    button.textContent = "✓";
    recordUsage(asset.asset_id, "selected", { destination: "material_pack" });
  }
  $("selection-count").textContent = `已選 ${visualState.selected.size} 張`;
  $("export-pack").disabled = visualState.selected.size === 0;
  $("batch-confirm").disabled = visualState.selected.size === 0;
}

async function recordUsage(assetId, action, context = {}) {
  try { return await requestJSON(`/api/visual-assets/${encodeURIComponent(assetId)}/usage`, { method: "POST", body: JSON.stringify({ action, context }) }); } catch { return null; }
}

async function loadDashboard() {
  setStatus("正在讀取視覺資料庫…");
  try {
    const data = await requestJSON("/api/visual-assets/dashboard");
    const stats = $("visual-stats");
    stats.replaceChildren();
    [["全部照片", data.total, "cyan"], ["待確認", data.counts.pending, "yellow"], ["重複圖片", data.counts.duplicates, "red"], ["高品質素材", data.counts.high_quality, "green"]].forEach(([label, count, color]) => {
      const card = node("article", `stat-card is-${color}`);
      card.append(node("span", null, label), node("strong", null, String(count)));
      stats.appendChild(card);
    });
    $("count-people").textContent = data.counts.people;
    $("count-clubs").textContent = data.counts.clubs;
    $("count-events").textContent = data.counts.events;
    $("count-scenes").textContent = data.counts.scenes;
    renderAssetGrid($("recent-assets"), data.recent, { empty: "上傳第一張照片後，會顯示在這裡" });
    setStatus(`資料庫共有 ${data.total} 張照片`, "green");
  } catch (error) { setStatus(error.message, "red"); }
}

function setUploadFiles(files, mode = "batch") {
  const extensions = /\.(jpe?g|png|webp|gif|avif|jfif|cr2|arw|dng|raw|heic|heif|psd|svg|tiff?|bmp|ico|ppm|mp4|mov|webm|m4v|avi|mkv|mts|m2ts|3gp|wmv|flv|mxf|rm|mp3|wav|m4a|flac|ogg|aac|wma|aiff?|opus|mid|midi|pdf|doc|docx|odt|ods|odp|oxps|xps|rtf|pptx|xlsx|gs|txt|md|csv|json|jsonl|yaml|yml|tsv|html|xml|ini|log|ics|srt|lrc|edl|fcpxml|sxml|rels|url|zip|7z|wfp|wfpbundle|bdm|cpi|mpl|aep|prproj|pds|prfpset|nbeffect|cfpreset|mogrt|prm|obj|gltf|glb|blend|fbx|dae|stl|3ds|ttf|ttc|otf|woff2?)$/i;
  const accepted = [...files].filter((file) => file.type.startsWith("image/") || file.type.startsWith("video/") || file.type.startsWith("audio/") || extensions.test(file.name));
  visualState.uploadFiles = accepted.slice(0, mode === "folder" ? 300 : 30);
  visualState.uploadMode = mode;
  $("import-mode").textContent = mode === "folder" ? "資料夾匯入：保留相對路徑與 manifest" : mode === "camera" ? "手機拍照上傳" : "一般批次上傳";
  const host = $("upload-preview");
  host.replaceChildren();
  visualState.uploadFiles.slice(0, 60).forEach((file) => {
    const figure = document.createElement("figure");
    if (file.type.startsWith("image/")) {
      const image = document.createElement("img");
      const url = URL.createObjectURL(file); image.src = url; image.alt = file.name;
      image.onload = () => URL.revokeObjectURL(url); figure.appendChild(image);
    } else figure.appendChild(node("div", "file-placeholder", file.type.startsWith("video/") ? "VIDEO" : "DOCUMENT"));
    figure.appendChild(node("figcaption", null, file.webkitRelativePath || file.name));
    host.appendChild(figure);
  });
  if (visualState.uploadFiles.length > 60) host.appendChild(node("p", "muted", `另有 ${visualState.uploadFiles.length - 60} 個素材，將在背景分批匯入`));
  setStatus(visualState.uploadFiles.length ? `已選擇 ${visualState.uploadFiles.length} 個素材` : "尚未選擇素材");
}

function uploadFiles() {
  if (!visualState.uploadFiles.length) { setStatus("請先選擇素材", "yellow"); return; }
  if (visualState.uploadMode === "folder") { uploadFolderBatches(); return; }
  const data = new FormData();
  visualState.uploadFiles.forEach((file) => data.append("files", file, file.name));
  data.append("school", $("upload-school").value.trim());
  data.append("club", $("upload-club").value.trim());
  data.append("source", $("upload-source").value.trim());
  data.append("privacy", $("upload-privacy").value);
  data.append("commercial_use", $("upload-commercial").value);
  data.append("auto_analyze", "true");
  const signature = visualState.uploadFiles.map((file) => `${file.webkitRelativePath || file.name}:${file.size}:${file.lastModified}`).join("|");
  let hash = 2166136261; for (let i = 0; i < signature.length; i += 1) hash = Math.imul(hash ^ signature.charCodeAt(i), 16777619);
  const idempotencyKey = `browser-${(hash >>> 0).toString(16)}-${visualState.uploadFiles.length}`;
  data.append("idempotency_key", idempotencyKey);
  let endpoint = "/api/visual-assets/upload";
  const xhr = new XMLHttpRequest();
  $("upload-progress").hidden = false;
  $("upload-progress-text").textContent = "正在上傳原圖（不覆寫）…";
  xhr.open("POST", endpoint);
  xhr.upload.onprogress = (event) => {
    if (!event.lengthComputable) return;
    const percent = Math.round(event.loaded / event.total * 100);
    $("upload-progress-bar").style.width = `${percent}%`;
    $("upload-progress-text").textContent = `上傳中 ${percent}%`;
  };
  xhr.onerror = () => { setStatus("上傳連線中斷，原圖若已完成寫入仍會保留", "red"); };
  xhr.onload = () => {
    let result = {};
    try { result = JSON.parse(xhr.responseText || "{}"); } catch { result = {}; }
    if (xhr.status === 401) { showGate(); return; }
    if (xhr.status < 200 || xhr.status >= 300) {
      const message = result.detail && (result.detail.message || result.detail) || (result.errors || []).map((e) => e.message).join("；") || "上傳失敗";
      setStatus(message, "red");
      return;
    }
    $("upload-progress-bar").style.width = "100%";
    const saved = result.created === undefined ? (result.items || []).length : result.created + (Number(result.skipped) || 0);
    $("upload-progress-text").textContent = `已保存 ${saved} 個原始素材，正在分析…`;
    renderAnalysisRows(result.items || []);
    (result.items || []).forEach((item) => pollAnalysis(item.asset_id));
    const failures = (result.errors || []).length;
    setStatus(`已保存 ${saved} 個；${failures ? `${failures} 個失敗可用同一資料夾單獨重試` : "辨識結果會逐筆更新"}`, failures ? "yellow" : "green");
  };
  xhr.send(data);
}

async function uploadFolderBatches() {
  const files = visualState.uploadFiles;
  const allItems = files.map((file, index) => ({ client_key: file.webkitRelativePath || `${index}:${file.name}`, relative_path: file.webkitRelativePath || file.name, filename: file.name, size: file.size, last_modified: new Date(file.lastModified).toISOString() }));
  const signature = allItems.map((item) => `${item.relative_path}:${item.size}:${item.last_modified}`).join("|");
  let hash = 2166136261; for (let i = 0; i < signature.length; i += 1) hash = Math.imul(hash ^ signature.charCodeAt(i), 16777619);
  const key = `folder-${(hash >>> 0).toString(16)}-${files.length}`;
  const created = []; const errors = [];
  $("upload-progress").hidden = false;
  for (let offset = 0; offset < files.length; offset += 30) {
    const chunk = files.slice(offset, offset + 30); const entries = allItems.slice(offset, offset + 30);
    const data = new FormData(); chunk.forEach((file) => data.append("files", file, file.name));
    data.append("manifest", JSON.stringify({ total_count: files.length, items: entries }));
    data.append("idempotency_key", key); data.append("root_name", (files[0].webkitRelativePath || "folder").split("/")[0]);
    data.append("school", $("upload-school").value.trim()); data.append("club", $("upload-club").value.trim());
    data.append("source", $("upload-source").value.trim() || "folder_import"); data.append("privacy", $("upload-privacy").value);
    data.append("commercial_use", $("upload-commercial").value); data.append("auto_analyze", "true"); data.append("resync", "false");
    $("upload-progress-text").textContent = `資料夾匯入 ${Math.min(offset + chunk.length, files.length)} / ${files.length}`;
    let response;
    try { response = await fetch("/api/visual-assets/import", { method: "POST", body: data }); }
    catch { errors.push(...entries.map((entry) => ({ client_key: entry.client_key, message: "連線中斷；重選同一資料夾即可續傳" }))); continue; }
    let result = {}; try { result = await response.json(); } catch { result = {}; }
    if (!response.ok && !(result.items || []).length) { errors.push(...(result.errors || [{ message: (result.detail && (result.detail.message || result.detail)) || "匯入失敗" }])); continue; }
    created.push(...(result.items || []), ...(result.skipped || [])); errors.push(...(result.errors || []));
    $("upload-progress-bar").style.width = `${Math.round(Math.min(offset + chunk.length, files.length) / files.length * 100)}%`;
  }
  renderAnalysisRows(created); created.forEach((item) => pollAnalysis(item.asset_id));
  setStatus(`資料夾已處理 ${files.length} 個：${created.length} 個可用、${errors.length} 個失敗可重選後續傳`, errors.length ? "yellow" : "green");
}

function renderAnalysisRows(items) {
  const host = $("analysis-results");
  host.replaceChildren();
  items.forEach((asset) => host.appendChild(analysisRow(asset)));
}

function analysisRow(asset) {
  const row = node("article", "analysis-row");
  row.dataset.assetId = asset.asset_id;
  const image = document.createElement("img"); image.src = asset.thumbnail_url; image.alt = asset.original_filename;
  const info = node("div"); info.append(node("h3", null, asset.original_filename), node("p", null, analysisProgressText(asset)));
  const inspect = node("button", "ghost", "查看"); inspect.type = "button"; inspect.addEventListener("click", () => openAsset(asset.asset_id));
  row.append(image, info, inspect);
  return row;
}

function analysisProgressText(asset) {
  const job = asset.analysis_job;
  if (job && job.status === "running") return `${statusLabel(asset.analysis_status)} · ${job.progress}% · ${job.stage}`;
  return `${statusLabel(asset.analysis_status)} · 畫質 ${Math.round(asset.quality_score || 0)}`;
}

function pollAnalysis(assetId, attempts = 0) {
  if (visualState.pollers.has(assetId)) clearTimeout(visualState.pollers.get(assetId));
  const timer = setTimeout(async () => {
    try {
      const asset = await requestJSON(`/api/visual-assets/${encodeURIComponent(assetId)}`);
      const current = document.querySelector(`.analysis-row[data-asset-id="${CSS.escape(assetId)}"]`);
      if (current) current.replaceWith(analysisRow(asset));
      const active = ["local_complete", "analyzing", "processing_entities"].includes(asset.analysis_status);
      if (active && attempts < 45) pollAnalysis(assetId, attempts + 1);
    } catch { /* polling is best effort */ }
  }, attempts < 3 ? 1100 : 2200);
  visualState.pollers.set(assetId, timer);
}

function searchParams() {
  const mapping = {
    q: "search-query", person: "filter-person", scene: "filter-scene", school: "filter-school", club: "filter-club",
    event: "filter-event", ratio: "filter-ratio", quality_min: "filter-quality", commercial_use: "filter-commercial",
    date_from: "filter-date-from", date_to: "filter-date-to", duplicate: "filter-duplicate",
  };
  const params = new URLSearchParams();
  Object.entries(mapping).forEach(([key, id]) => { const value = $(id).value.trim(); if (value && !(key === "quality_min" && value === "0")) params.set(key, value); });
  params.set("limit", "60");
  return params;
}

async function runSearch() {
  setStatus("正在比對文字、實體、語意與畫質…");
  $("question-card").hidden = true;
  try {
    const queryImage = $("query-image").files && $("query-image").files[0];
    let data;
    if (queryImage) {
      const form = new FormData(); form.append("file", queryImage, queryImage.name);
      const resp = await fetch("/api/visual-assets/search-by-image?limit=60", { method: "POST", body: form });
      if (!resp.ok) throw new Error(await detailFromResponse(resp));
      data = await resp.json();
      $("search-conditions").textContent = "以圖搜圖：依感知雜湊、構圖與色彩排序";
    } else {
      data = await requestJSON(`/api/visual-assets/search?${searchParams().toString()}`);
      const conditionNames = { query: "搜尋", school: "學校", club: "社團", person: "人物", scene: "場景", event: "活動", date_from: "起始日期", date_to: "結束日期", ratio: "比例", quality_min: "最低畫質", commercial_use: "商用權限" };
      const conditions = Object.entries(data.parsed_conditions || {}).filter(([, value]) => value !== "" && value !== 0).map(([key, value]) => `${conditionNames[key] || key}：${value}`);
      $("search-conditions").textContent = conditions.join(" · ") || "目前未指定條件";
    }
    visualState.lastItems = data.items || [];
    $("search-count").textContent = `找到 ${data.total || 0} 張`;
    renderAssetGrid($("search-results"), visualState.lastItems, { selectable: true });
    setStatus(data.total ? "搜尋完成；每張卡片都會說明推薦原因" : "找不到符合條件的圖片，請放寬一個條件", data.total ? "green" : "yellow");
  } catch (error) { setStatus(error.message, "red"); }
}

async function loadReview(kind) {
  document.querySelectorAll("[data-review]").forEach((button) => button.classList.toggle("is-active", button.dataset.review === kind));
  setStatus("正在整理待審核資料…");
  try {
    let path = `/api/visual-assets/review-queue?limit=60&status=${encodeURIComponent(kind)}`;
    if (kind === "duplicate") path = "/api/visual-assets/search?limit=60&duplicate=only";
    if (kind === "high-quality") path = "/api/visual-assets/search?limit=60&quality_min=75&duplicate=exclude";
    const data = await requestJSON(path);
    const items = data.items || [];
    renderAssetGrid($("review-results"), items, { empty: "目前沒有這類素材", selectable: true, confirmable: !["verified","failed"].includes(kind), retryable: kind === "failed" });
    setStatus(`顯示 ${items.length} 張`, "green");
  } catch (error) { setStatus(error.message, "red"); }
}

const INVENTORY_LABELS = {
  images: "圖片", videos: "影片", audio: "音訊", other_files: "其他檔案", documents: "文件", artifacts: "Artifact", knowledge_sources: "知識來源",
  clubs: "社團", schools: "學校", people: "人物", events: "活動", dates: "日期",
  duplicate_assets: "重複素材", missing_sources: "缺少來源", pending_review: "待確認", analysis_failed: "無法分析",
};

async function loadOrganization(refresh = false) {
  setStatus(refresh ? "正在重新掃描現有資料與 SHA-256…" : "正在讀取資料盤點…");
  try {
    const report = await requestJSON(`/api/data-organization/inventory?refresh=${refresh ? "true" : "false"}`);
    const host = $("inventory-stats"); host.replaceChildren();
    Object.entries(INVENTORY_LABELS).forEach(([key, label]) => {
      const card = node("article", "inventory-card");
      card.append(node("span", null, label), node("strong", null, String((report.statistics || {})[key] || 0)));
      host.appendChild(card);
    });
    $("organization-run-status").textContent = `最近盤點：${report.created_at || "剛剛"} · 同步失敗 ${(report.breakdown || {}).sync_failed || 0}`;
    await loadOrganizationQueue("verified");
    setStatus("資料盤點完成；統計來自目前資料庫與實際檔案", "green");
  } catch (error) { setStatus(error.message, "red"); }
}

async function loadOrganizationQueue(category) {
  document.querySelectorAll("[data-organization]").forEach((button) => button.classList.toggle("is-active", button.dataset.organization === category));
  try {
    const data = await requestJSON(`/api/data-organization/queue?category=${encodeURIComponent(category)}&limit=60`);
    renderAssetGrid($("organization-results"), data.items || [], { empty: "目前沒有這類資料", selectable: true, confirmable: category !== "verified", retryable: category === "failed" });
  } catch (error) { setStatus(error.message, "red"); }
}

async function organizeExisting() {
  setStatus("正在建立來源、lineage、Entity Graph 與 review queue…");
  $("organize-existing").disabled = true;
  try {
    const result = await requestJSON("/api/data-organization/organize", { method: "POST", body: JSON.stringify({ idempotency_key: `organize-${Date.now()}`, project_id: "" }) });
    visualState.organizationRun = result.id;
    await loadOrganization(true);
    $("organization-run-status").textContent = `最近同步：${result.status} · 成功 ${result.imported_count || 0} · 失敗 ${result.failed_count || 0}`;
    setStatus(result.failed_count ? "部分資料整理失敗；成功項目已保留，可只重試失敗項目" : "現有資料已完成非破壞式整理", result.failed_count ? "yellow" : "green");
  } catch (error) { setStatus(error.message, "red"); }
  finally { $("organize-existing").disabled = false; }
}

function evidenceText(value) {
  if (!value) return "未提供額外證據";
  if (typeof value === "string") return value;
  return Object.entries(value).map(([key, item]) => `${key}: ${typeof item === "object" ? JSON.stringify(item) : item}`).join("；");
}

async function openAsset(assetId) {
  $("asset-modal").hidden = false;
  $("asset-modal-body").replaceChildren(node("p", "muted", "正在讀取圖片與辨識證據…"));
  try {
    const asset = await requestJSON(`/api/visual-assets/${encodeURIComponent(assetId)}`);
    $("asset-modal-title").textContent = asset.original_filename || "圖片分析";
    renderAssetDetail(asset);
  } catch (error) { $("asset-modal-body").replaceChildren(emptyCard(error.message)); }
}

function renderAssetDetail(asset) {
  const host = $("asset-modal-body"); host.replaceChildren();
  const preview = node("div", "detail-preview");
  const image = document.createElement("img"); image.src = asset.asset_type === "image" ? asset.original_url : asset.thumbnail_url; image.alt = asset.original_filename;
  preview.appendChild(image);
  const sizeActions = node("div", "asset-actions");
  (asset.asset_type === "image" ? ["original", "1:1", "4:5", "9:16", "16:9"] : ["original", "thumbnail"]).forEach((variant) => {
    const link = node("a", null, variant === "original" ? "原圖" : variant);
    link.href = `/api/visual-assets/${encodeURIComponent(asset.asset_id)}/file?variant=${encodeURIComponent(variant)}&download=true`;
    sizeActions.appendChild(link);
  });
  preview.appendChild(sizeActions);
  const details = node("div");
  const summary = node("section", "detail-section");
  summary.appendChild(node("h3", null, "品質與狀態"));
  const row = node("div", "badge-row"); row.append(badge(asset.analysis_status), badge(asset.review_status), badge(asset.date_status, `日期：${statusLabel(asset.date_status)}`));
  summary.append(row, node("p", "muted", `${asset.width}×${asset.height} · 畫質 ${Math.round(asset.quality_score)} · 清晰 ${Math.round(asset.blur_score)} · 亮度 ${Math.round(asset.brightness_score)}`));
  if (asset.duplicate_of) summary.appendChild(node("p", "status-badge conflicted", `重複於 ${asset.duplicate_of}`));
  details.appendChild(summary);

  const observations = node("section", "detail-section"); observations.appendChild(node("h3", null, "辨識人物／場景／活動／社團"));
  if (!(asset.observations || []).length) observations.appendChild(node("p", "muted", "尚無視覺辨識結果"));
  (asset.observations || []).forEach((obs) => observations.appendChild(observationRow(asset, obs)));
  details.appendChild(observations);

  const dates = node("section", "detail-section"); dates.appendChild(node("h3", null, `日期候選 · ${statusLabel(asset.date_status)}`));
  (asset.date_candidates || []).forEach((date) => {
    const item = node("div", "observation");
    item.append(badge(date.status), node("strong", null, ` ${date.value}`), node("p", null, `來源：${date.source} · 信心 ${Math.round((date.confidence || 0) * 100)}% · ${date.evidence || ""}`));
    if (date.status !== "verified") {
      const button = node("button", "ghost", "確認此日期"); button.type = "button";
      button.addEventListener("click", () => confirmEntity(asset, { entity_type: "date", candidate_label: date.value, action: "confirm" }));
      item.appendChild(button);
    }
    dates.appendChild(item);
  });
  if (!(asset.date_candidates || []).length) dates.appendChild(node("p", "muted", "沒有日期證據；可在下方手動補充"));
  details.appendChild(dates);

  const ocr = node("section", "detail-section"); ocr.append(node("h3", null, "OCR 文字"), node("p", "ocr-text", asset.ocr_text || "未讀取到文字")); details.appendChild(ocr);
  details.appendChild(correctionForm(asset));
  host.append(preview, details);
}

function observationRow(asset, obs) {
  const item = node("div", "observation");
  const title = node("div", "badge-row"); title.append(badge(obs.status), node("strong", null, `${obs.entity_type} · ${obs.label || "未命名候選"}`));
  item.append(title, node("p", null, `來源：${obs.source} · 信心 ${Math.round((obs.confidence || 0) * 100)}%`), node("p", null, `證據：${evidenceText(obs.evidence)}`));
  if (obs.status !== "verified" && obs.review_action !== "ignored") {
    const actions = node("div", "observation-actions");
    if (!(obs.entity_type === "person" && !obs.entity_id)) {
      const confirm = node("button", null, "確認"); confirm.type = "button";
      confirm.addEventListener("click", () => confirmEntity(asset, { entity_type: obs.entity_type, entity_id: obs.entity_id || "", candidate_label: obs.label.replace(/^可能是/, ""), observation_id: obs.id, action: "confirm" }));
      actions.appendChild(confirm);
    }
    const ignore = node("button", null, "忽略"); ignore.type = "button";
    ignore.addEventListener("click", () => confirmEntity(asset, { entity_type: ["logo"].includes(obs.entity_type) ? "club" : obs.entity_type, observation_id: obs.id, action: "ignore" }));
    actions.appendChild(ignore);
    item.appendChild(actions);
  }
  return item;
}

function correctionForm(asset) {
  const section = node("section", "detail-section"); section.appendChild(node("h3", null, "手動確認／修正"));
  const form = document.createElement("form"); form.className = "metadata-grid";
  const typeLabel = node("label"); typeLabel.appendChild(node("span", null, "類型"));
  const type = document.createElement("select"); ["person", "scene", "event", "club", "date"].forEach((value) => { const option = node("option", null, ({ person: "人物", scene: "場景", event: "活動", club: "社團", date: "日期" })[value]); option.value = value; type.appendChild(option); }); typeLabel.appendChild(type);
  const valueLabel = node("label"); valueLabel.appendChild(node("span", null, "正確名稱／日期")); const value = document.createElement("input"); value.required = true; value.maxLength = 300; valueLabel.appendChild(value);
  const schoolLabel = node("label"); schoolLabel.appendChild(node("span", null, "學校（人物／社團必填）")); const school = document.createElement("input"); school.value = "淡江大學"; school.maxLength = 120; schoolLabel.appendChild(school);
  const submit = node("button", "primary", "確認並記錄來源"); submit.type = "submit";
  form.append(typeLabel, valueLabel, schoolLabel, submit);
  form.addEventListener("submit", (event) => { event.preventDefault(); confirmEntity(asset, { entity_type: type.value, candidate_label: value.value.trim(), values: { name: value.value.trim(), value: value.value.trim(), school: school.value.trim(), use_as_reference: type.value === "person" }, action: "correct", reason: "使用者在圖片分析卡手動修正" }); });
  section.appendChild(form); return section;
}

async function confirmEntity(asset, payload) {
  setStatus("正在保存人工確認與修正紀錄…");
  try {
    const updated = await requestJSON("/api/entities/confirm", { method: "POST", body: JSON.stringify({ asset_id: asset.asset_id, entity_id: "", candidate_label: "", observation_id: "", values: {}, reason: "使用者在圖片分析卡確認", ...payload }) });
    renderAssetDetail(updated);
    setStatus("已記錄人工確認；原始 AI 結果與證據仍保留", "green");
  } catch (error) { setStatus(error.message, "red"); }
}

async function exportPack() {
  if (!visualState.selected.size) return;
  setStatus("正在建立圖片、JSON 與 CSV 素材包…");
  try {
    const resp = await fetch("/api/visual-assets/export", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ asset_ids: [...visualState.selected], rendition: $("pack-rendition").value }) });
    if (!resp.ok) throw new Error(await detailFromResponse(resp));
    const blob = await resp.blob();
    const url = URL.createObjectURL(blob); const link = document.createElement("a"); link.href = url; link.download = "visual-assets.zip"; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    setStatus("素材包已建立並下載", "green");
  } catch (error) { setStatus(error.message, "red"); }
}

async function batchConfirm() {
  const ids = [...visualState.selected];
  if (!ids.length) return;
  setStatus(`正在確認 ${ids.length} 個素材…`);
  const results = await Promise.allSettled(ids.map((id) => requestJSON(`/api/visual-assets/${encodeURIComponent(id)}/confirm`, { method: "POST", body: JSON.stringify({ reason: "使用者批次確認" }) })));
  const failed = results.filter((result) => result.status === "rejected").length;
  if (!failed) visualState.selected.clear();
  setStatus(failed ? `${ids.length - failed} 個已確認；${failed} 個因衝突需逐筆修正` : `${ids.length} 個素材已確認`, failed ? "yellow" : "green");
  loadReview("pending_review");
}

async function boot() {
  try {
    const auth = await requestJSON("/api/auth");
    if (!auth.authenticated) { showGate(); return; }
    $("visual-gate").hidden = true; $("visual-app").hidden = false; loadDashboard();
  } catch { showGate(); }
}

$("visual-gate-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await requestJSON("/api/auth", { method: "POST", body: JSON.stringify({ token: $("visual-token").value }) });
    $("visual-token").value = ""; $("visual-gate").hidden = true; $("visual-app").hidden = false; loadDashboard();
  } catch (error) { showGate(error.message); }
});
document.querySelectorAll(".visual-tabs button").forEach((button) => button.addEventListener("click", () => showPanel(button.dataset.panel)));
document.querySelectorAll("[data-open-panel]").forEach((button) => button.addEventListener("click", () => showPanel(button.dataset.openPanel)));
$("visual-files").addEventListener("change", (event) => setUploadFiles(event.target.files || [], "batch"));
$("camera-files").addEventListener("change", (event) => setUploadFiles(event.target.files || [], "camera"));
$("folder-files").addEventListener("change", (event) => setUploadFiles(event.target.files || [], "folder"));
$("clear-upload").addEventListener("click", () => { visualState.uploadFiles = []; ["visual-files","camera-files","folder-files"].forEach((id) => { $(id).value = ""; }); $("upload-preview").replaceChildren(); setStatus("已清除待上傳清單"); });
$("upload-form").addEventListener("submit", (event) => { event.preventDefault(); uploadFiles(); });
const dropZone = $("drop-zone");
["dragenter", "dragover"].forEach((name) => dropZone.addEventListener(name, (event) => { event.preventDefault(); dropZone.classList.add("is-dragging"); }));
["dragleave", "drop"].forEach((name) => dropZone.addEventListener(name, (event) => { event.preventDefault(); dropZone.classList.remove("is-dragging"); }));
dropZone.addEventListener("drop", (event) => setUploadFiles(event.dataTransfer.files || []));
document.addEventListener("paste", (event) => {
  if (visualState.panel !== "upload") return;
  const images = [...(event.clipboardData && event.clipboardData.files || [])].filter((file) => file.type.startsWith("image/"));
  if (images.length) { event.preventDefault(); setUploadFiles([...visualState.uploadFiles, ...images]); }
});
$("search-form").addEventListener("submit", (event) => { event.preventDefault(); runSearch(); });
$("query-image").addEventListener("change", (event) => { const file = event.target.files && event.target.files[0]; $("query-image-name").textContent = file ? file.name : "未選擇查詢圖片"; });
document.querySelectorAll("[data-search]").forEach((button) => button.addEventListener("click", () => { $("search-query").value = button.dataset.search; runSearch(); }));
document.querySelectorAll("[data-review]").forEach((button) => button.addEventListener("click", () => loadReview(button.dataset.review)));
document.querySelectorAll("[data-organization]").forEach((button) => button.addEventListener("click", () => loadOrganizationQueue(button.dataset.organization)));
$("refresh-inventory").addEventListener("click", () => loadOrganization(true));
$("organize-existing").addEventListener("click", organizeExisting);
document.querySelectorAll("[data-filter]").forEach((button) => button.addEventListener("click", () => { showPanel("search"); $("search-query").value = ({ person: "人物清楚", club: "社團活動", event: "活動現場", scene: "校園 教室 舞台" })[button.dataset.filter] || ""; runSearch(); }));
$("export-pack").addEventListener("click", exportPack);
$("batch-confirm").addEventListener("click", batchConfirm);
$("asset-modal-close").addEventListener("click", () => { $("asset-modal").hidden = true; });
$("asset-modal").addEventListener("click", (event) => { if (event.target === $("asset-modal")) $("asset-modal").hidden = true; });
boot();
