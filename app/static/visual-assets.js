"use strict";

const $ = (id) => document.getElementById(id);
const visualState = { panel: "dashboard", uploadFiles: [], selected: new Set(), lastItems: [], pollers: new Map() };

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
  if (name === "review") loadReview("pending");
}

function statusLabel(value) {
  return ({ confirmed: "已確認", complete: "分析完成", possible: "可能", pending: "待確認", conflict: "衝突", ignored: "已忽略", analyzing: "分析中", processing_entities: "建立關聯中", local_complete: "本機檢查完成", needs_vision_config: "AI 分析未啟用", analysis_failed: "分析失敗" })[value] || value || "待確認";
}

function badge(value, label = "") {
  return node("span", `status-badge ${value || "pending"}`, label || statusLabel(value));
}

function emptyCard(text) {
  return node("p", "empty-visual", text);
}

function assetCard(asset, { selectable = false } = {}) {
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
  if (asset.review_status && asset.review_status !== "pending") badges.appendChild(badge(asset.review_status));
  if (asset.duplicate_of) badges.appendChild(badge("conflict", "重複圖片"));
  if (asset.commercial_use === "allowed") badges.appendChild(badge("confirmed", "可商用"));
  if (asset.commercial_use === "unknown") badges.appendChild(badge("possible", "權限待確認"));
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

function setUploadFiles(files) {
  const accepted = [...files].filter((file) => ["image/jpeg", "image/png", "image/webp"].includes(file.type));
  visualState.uploadFiles = accepted.slice(0, 30);
  const host = $("upload-preview");
  host.replaceChildren();
  visualState.uploadFiles.forEach((file) => {
    const figure = document.createElement("figure");
    const image = document.createElement("img");
    const url = URL.createObjectURL(file);
    image.src = url;
    image.alt = file.name;
    image.onload = () => URL.revokeObjectURL(url);
    figure.append(image, node("figcaption", null, file.name));
    host.appendChild(figure);
  });
  setStatus(visualState.uploadFiles.length ? `已選擇 ${visualState.uploadFiles.length} 張圖片` : "尚未選擇圖片");
}

function uploadFiles() {
  if (!visualState.uploadFiles.length) { setStatus("請先選擇圖片", "yellow"); return; }
  const data = new FormData();
  visualState.uploadFiles.forEach((file) => data.append("files", file, file.name));
  data.append("school", $("upload-school").value.trim());
  data.append("club", $("upload-club").value.trim());
  data.append("source", $("upload-source").value.trim());
  data.append("privacy", $("upload-privacy").value);
  data.append("commercial_use", $("upload-commercial").value);
  data.append("auto_analyze", "true");
  const xhr = new XMLHttpRequest();
  $("upload-progress").hidden = false;
  $("upload-progress-text").textContent = "正在上傳原圖（不覆寫）…";
  xhr.open("POST", "/api/visual-assets/upload");
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
    $("upload-progress-text").textContent = `已保存 ${result.created} 張原圖，正在分析…`;
    renderAnalysisRows(result.items || []);
    (result.items || []).forEach((item) => pollAnalysis(item.asset_id));
    setStatus(`已保存 ${result.created} 張；辨識結果會逐張更新`, "green");
  };
  xhr.send(data);
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
    let path = "/api/visual-assets/search?limit=60";
    if (kind === "duplicate") path += "&duplicate=only";
    if (kind === "high-quality") path += "&quality_min=75&duplicate=exclude";
    const data = await requestJSON(path);
    let items = data.items || [];
    if (kind === "pending") items = items.filter((item) => ["pending", "possible", "conflict"].includes(item.review_status) || ["local_complete", "needs_vision_config", "analysis_failed"].includes(item.analysis_status));
    renderAssetGrid($("review-results"), items, { empty: "目前沒有這類圖片" });
    setStatus(`顯示 ${items.length} 張`, "green");
  } catch (error) { setStatus(error.message, "red"); }
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
  const image = document.createElement("img"); image.src = asset.original_url; image.alt = asset.original_filename;
  preview.appendChild(image);
  const sizeActions = node("div", "asset-actions");
  ["original", "1:1", "4:5", "9:16", "16:9"].forEach((variant) => {
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
  if (asset.duplicate_of) summary.appendChild(node("p", "status-badge conflict", `重複於 ${asset.duplicate_of}`));
  details.appendChild(summary);

  const observations = node("section", "detail-section"); observations.appendChild(node("h3", null, "辨識人物／場景／活動／社團"));
  if (!(asset.observations || []).length) observations.appendChild(node("p", "muted", "尚無視覺辨識結果"));
  (asset.observations || []).forEach((obs) => observations.appendChild(observationRow(asset, obs)));
  details.appendChild(observations);

  const dates = node("section", "detail-section"); dates.appendChild(node("h3", null, `日期候選 · ${statusLabel(asset.date_status)}`));
  (asset.date_candidates || []).forEach((date) => {
    const item = node("div", "observation");
    item.append(badge(date.status), node("strong", null, ` ${date.value}`), node("p", null, `來源：${date.source} · 信心 ${Math.round((date.confidence || 0) * 100)}% · ${date.evidence || ""}`));
    if (date.status !== "confirmed") {
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
  if (!["confirmed", "ignored"].includes(obs.status)) {
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
$("visual-files").addEventListener("change", (event) => setUploadFiles(event.target.files || []));
$("clear-upload").addEventListener("click", () => { visualState.uploadFiles = []; $("visual-files").value = ""; $("upload-preview").replaceChildren(); setStatus("已清除待上傳清單"); });
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
document.querySelectorAll("[data-filter]").forEach((button) => button.addEventListener("click", () => { showPanel("search"); $("search-query").value = ({ person: "人物清楚", club: "社團活動", event: "活動現場", scene: "校園 教室 舞台" })[button.dataset.filter] || ""; runSearch(); }));
$("export-pack").addEventListener("click", exportPack);
$("asset-modal-close").addEventListener("click", () => { $("asset-modal").hidden = true; });
$("asset-modal").addEventListener("click", (event) => { if (event.target === $("asset-modal")) $("asset-modal").hidden = true; });
boot();
