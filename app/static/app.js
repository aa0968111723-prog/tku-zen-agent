/* 淡江大學領袖禪學社 · 工作台前端 */

const $ = (id) => document.getElementById(id);

const FILE_ICONS = { xlsx: "▦", docx: "▤", pptx: "▣", gs: "⚡", md: "≡", pdf: "▪" };
const CARD_LANGS = {
  "ig-post": "貼文草稿",
  "ig-carousel": "輪播草稿",
  "ig-story": "限動草稿",
  "reels-script": "Reels 腳本",
  "content-calendar": "內容月曆",
  "ab-test": "A/B 測試",
  "image-prompt": "圖像提示",
  "video-prompt": "影片提示",
};
const RESEARCH_HEADS = [
  "其他學校怎麼做",
  "為什麼可能有效",
  "淡江可以怎麼改良",
  "哪些內容不能直接照抄",
];
const PROMPTS = {
  event:
    "幫我規劃一場社團活動。請先確認活動類型（期初茶會／社課／演講／營隊），再產出時程、分工、活動細流與待辦清單。",
  lookup:
    "幫我查社團資料：我們社團是做什麼的、活動有哪些、招生怎麼做、幹部最常問的規定是什麼。",
  docs:
    "幫我產生社團文件。請先問我要做企劃書、表單、簡報還是預算表，確認需求後再產出。",
};
const MODE_HINTS = {
  ask: "描述你想完成的事，AI 會先整理成可確認的任務摘要。",
  generate: "直接描述想要的成品；若關鍵資訊不足，會先問 1–3 個問題。",
  template: "說明要重複使用的格式，例如活動企劃、會議紀錄或社群貼文。",
  plan: "描述目標與期限，AI 會整理成可執行的任務清單。",
};
const FLOW_STEPS = [
  ["understand", "已理解需求", "整理目標與預期成果"],
  ["facts", "確認必要資料", "只補會影響成果的資訊"],
  ["research", "查詢社團資料", "使用目前可用的相關資料"],
  ["draft", "產生初稿", "先完成可修改的版本"],
  ["verify", "檢查內容", "檢查待填資訊與內容品質"],
  ["repair", "修正問題", "只有需要時才進行"],
  ["done", "產出完成", "準備好進行下一步"],
];
const TOOL_PHASES = {
  search_knowledge: ["research", "查詢社團資料"],
  get_current_term: ["facts", "確認必要資料"],
  search_social_references: ["research", "查詢公開參考資料"],
  compare_social_strategies: ["research", "整理公開參考資料"],
  analyze_social_positioning: ["research", "分析公開參考資料"],
  create_document: ["draft", "產生文件初稿"],
  create_slides: ["draft", "產生簡報初稿"],
  create_spreadsheet: ["draft", "產生表格初稿"],
  create_google_form: ["draft", "產生表單初稿"],
  create_social_post: ["draft", "產生貼文初稿"],
  create_social_carousel: ["draft", "產生輪播初稿"],
  create_social_story: ["draft", "產生限動初稿"],
  create_reels_script: ["draft", "產生 Reels 腳本"],
};
const PLATFORM_FENCE = {
  貼文: "ig-post",
  輪播: "ig-carousel",
  限動: "ig-story",
  Reels: "reels-script",
};
const BUSY_TEXT = "系統忙碌中，請稍後再試";
const URL_RE = /https?:\/\/[^\s<>"'）)]+/gi;
const TECH_RE =
  /Exception|Traceback|TypeError|ValueError|HTTP\s*\d|Error:|NVIDIA|nvapi-|堆疊|\.py\b|連線中斷|伺服器錯誤|status\s*\d|api[_-]?key/i;

const state = {
  sessionId: null,
  busy: false,
  health: null,
  isAdmin: false,
  adminEndpoint: null,
  lastPrompt: "",
  panel: "home",
  toolSeq: 0,
  mode: "ask",
  preflight: null,
  projectId: null,
  activeTurn: null,
  abortController: null,
  taskSummary: null,
  attachment: null,
  voice: null,
};

/* ── DOM 小工具 ─────────────────────────────────────── */

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
}

function announce(text) {
  $("status-bar").textContent = text || "";
}

function scrollChat() {
  const box = $("chat");
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  requestAnimationFrame(() =>
    box.scrollTo({ top: box.scrollHeight, behavior: reduce ? "auto" : "smooth" })
  );
}

function applyViewport() {
  const vv = window.visualViewport;
  const h = vv ? Math.round(vv.height) : window.innerHeight;
  const top = vv ? Math.round(vv.offsetTop) : 0;
  document.documentElement.style.setProperty("--app-height", h + "px");
  document.documentElement.style.setProperty("--app-top", top + "px");
}

function missingStatus(resp) {
  return resp.status === 404 || resp.status === 405;
}

async function readDetail(resp) {
  try {
    const data = await resp.clone().json();
    if (typeof data.detail === "string" && data.detail.trim()) return data.detail.trim();
    if (Array.isArray(data.detail)) {
      const msg = data.detail
        .map((d) => (typeof d === "string" ? d : d.msg || ""))
        .filter(Boolean)
        .join("；");
      if (msg) return msg;
    }
  } catch {
    /* 非 JSON */
  }
  return "";
}

function friendlyError(text) {
  if (!text) return BUSY_TEXT;
  const t = String(text).trim();
  if (!t) return BUSY_TEXT;
  if (TECH_RE.test(t)) return BUSY_TEXT;
  if (/[\\/][\w.-]+\.\w+/.test(t) && /app[\\/]|File "/.test(t)) return BUSY_TEXT;
  return t;
}

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (options.body && !headers["Content-Type"]) headers["Content-Type"] = "application/json";
  const resp = await fetch(path, { ...options, headers });
  if (resp.status === 401) {
    const detail = await readDetail(resp);
    showGate(detail);
    const err = new Error("needs-auth");
    err.detail = detail;
    throw err;
  }
  return resp;
}

/* ── 複製 / 下載 ───────────────────────────────────── */

function fallbackCopy(text) {
  const ta = document.createElement("textarea");
  ta.value = text;
  ta.setAttribute("readonly", "");
  ta.style.position = "fixed";
  ta.style.left = "-9999px";
  document.body.appendChild(ta);
  ta.select();
  let ok = false;
  try {
    ok = document.execCommand("copy");
  } catch {
    ok = false;
  }
  ta.remove();
  return ok;
}

function copyText(text) {
  if (navigator.clipboard && typeof navigator.clipboard.writeText === "function") {
    return navigator.clipboard.writeText(text).then(
      () => true,
      () => fallbackCopy(text)
    );
  }
  return Promise.resolve(fallbackCopy(text));
}

function downloadText(filename, text) {
  const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function startFollowUp(instruction) {
  $("input").value = instruction;
  showPanel("chat");
  openPreflight(instruction);
}

function followUpBar(raw, kind) {
  const wrap = el("section", "next-actions");
  wrap.appendChild(el("h3", null, "接下來可以怎麼做？"));
  const actions = el("div", "card-actions");
  const options = kind === "document"
    ? [["修改這份", "請修改以下草稿，保留已確認內容並先說明會調整什麼：\n" + raw], ["產生網宣", "請依照以下文件產生一則 IG 網宣草稿：\n" + raw], ["產生簡報", "請依照以下文件產生簡報大綱與投影片草稿：\n" + raw]]
    : [["修改這份", "請修改以下草稿，保留已確認內容並先說明會調整什麼：\n" + raw], ["轉成輪播", "請把以下內容轉成 IG 輪播草稿；每頁以 --- 分隔，未知資料標示待填：\n" + raw], ["轉成 Reels", "請把以下內容轉成 Reels 腳本，分成開場、鏡頭、旁白、字幕與結尾 CTA：\n" + raw], ["產生簡報", "請依照以下內容產生簡報大綱與投影片草稿：\n" + raw]];
  options.forEach(([label, prompt]) => {
    const btn = el("button", null, label);
    btn.type = "button";
    btn.addEventListener("click", () => startFollowUp(prompt));
    actions.appendChild(btn);
  });
  wrap.appendChild(actions);
  return wrap;
}

function firstArtifact(turn) {
  return (turn._artifacts && turn._artifacts[0]) || null;
}

function bindCardActions(card, turn, raw) {
  const copyBtn = el("button", null, "複製");
  copyBtn.type = "button";
  copyBtn.addEventListener("click", async () => {
    const ok = await copyText(raw);
    copyBtn.textContent = ok ? "已複製" : "複製失敗";
    setTimeout(() => {
      copyBtn.textContent = "複製";
    }, 1400);
  });

  const dl = el("a", null, "下載");
  dl.hidden = true;
  const refreshDl = () => {
    const art = firstArtifact(turn);
    if (art && art.artifact_id) {
      dl.hidden = false;
      dl.href = "/api/download?artifact_id=" + encodeURIComponent(art.artifact_id);
      dl.removeAttribute("download");
    } else {
      dl.hidden = false;
      dl.removeAttribute("href");
      dl.onclick = (e) => {
        e.preventDefault();
        downloadText((card.dataset.kind || "draft") + ".txt", raw);
      };
    }
  };
  refreshDl();
  card._refreshDl = refreshDl;

  const retry = el("button", null, "重試");
  retry.type = "button";
  retry.addEventListener("click", () => {
    const prompt = turn.dataset.prompt || state.lastPrompt;
    if (prompt) send(prompt);
  });

  const bar = el("div", "card-actions");
  bar.append(copyBtn, dl, retry);
  card.appendChild(bar);
  card.appendChild(followUpBar(raw, card.dataset.kind === "document" ? "document" : "social"));
}

/* ── 網宣 / 研究卡片 ───────────────────────────────── */

function splitCarousel(text) {
  const dashed = text.split(/\n-{3,}\n/);
  if (dashed.length > 1) return dashed.map((s) => s.trim()).filter(Boolean);
  const numbered = text.split(/(?:^|\n)\s*\d+\s*[.\、\.\:：]\s+/).map((s) => s.trim()).filter(Boolean);
  if (numbered.length > 1) return numbered;
  const headed = text.split(/(?:^|\n)#{1,3}\s+/).map((s) => s.trim()).filter(Boolean);
  if (headed.length > 1) return headed;
  return [text.trim()].filter(Boolean);
}

function renderCarousel(card, pages) {
  let idx = 0;
  const body = el("div", "soc-body carousel-page");
  const ind = el("span", "page-ind");
  const show = () => {
    body.textContent = pages[idx] || "";
    ind.textContent = pages.length ? idx + 1 + " / " + pages.length : "";
  };
  const prev = el("button", null, "‹");
  prev.type = "button";
  prev.setAttribute("aria-label", "上一頁");
  const next = el("button", null, "›");
  next.type = "button";
  next.setAttribute("aria-label", "下一頁");
  prev.addEventListener("click", () => {
    idx = (idx - 1 + pages.length) % pages.length;
    show();
  });
  next.addEventListener("click", () => {
    idx = (idx + 1) % pages.length;
    show();
  });
  let x0 = 0;
  body.addEventListener(
    "touchstart",
    (e) => {
      x0 = e.changedTouches[0].clientX;
    },
    { passive: true }
  );
  body.addEventListener(
    "touchend",
    (e) => {
      const dx = e.changedTouches[0].clientX - x0;
      if (dx > 40) prev.click();
      else if (dx < -40) next.click();
    },
    { passive: true }
  );
  const meta = el("div", "carousel-meta");
  const pager = el("div", "pager");
  pager.append(prev, next);
  meta.append(pager, ind);
  card.append(body, meta);
  show();
}

function renderSocCard(turn, lang, raw) {
  const card = el("article", "soc-card");
  card.dataset.card = "1";
  card.dataset.kind = lang;
  const head = el("div", "card-head");
  head.append(el("div", "card-kind", lang.split("-").pop().slice(0, 2).toUpperCase()));
  head.append(el("div", "card-title", CARD_LANGS[lang] || lang));
  card.appendChild(head);
  if (lang === "ig-carousel") {
    renderCarousel(card, splitCarousel(raw));
  } else {
    const body = el("div", "soc-body", raw);
    card.appendChild(body);
  }
  bindCardActions(card, turn, raw);
  return card;
}

function headingKey(line) {
  const t = line
    .trim()
    .replace(/^#{1,6}\s*/, "")
    .replace(/^\*+|\*+$/g, "")
    .replace(/[:：]\s*$/, "")
    .trim();
  return RESEARCH_HEADS.includes(t) ? t : "";
}

function splitResearch(text) {
  const lines = text.split("\n");
  const hits = [];
  lines.forEach((line, i) => {
    const key = headingKey(line);
    if (key) hits.push({ key, i });
  });
  const uniq = new Set(hits.map((h) => h.key));
  if (uniq.size < 3) return null;
  const leftover = lines.slice(0, hits[0].i).join("\n").trim();
  const sections = hits.map((h, n) => {
    const end = n + 1 < hits.length ? hits[n + 1].i : lines.length;
    return { title: h.key, body: lines.slice(h.i + 1, end).join("\n").trim() };
  });
  return { leftover, sections };
}

function renderResearch(turn, parsed) {
  const wrap = el("div", "research-wrap");
  wrap.appendChild(el("h3", null, "參考資料（可收合）"));
  parsed.sections.forEach((sec, i) => {
    const d = el("details", "research-card");
    d.open = i === 0;
    d.append(el("summary", null, sec.title), el("div", "body", sec.body || "（無）"));
    wrap.appendChild(d);
  });
  bindCardActions(wrap, turn, parsed.sections.map((s) => s.title + "\n" + s.body).join("\n\n"));
  return wrap;
}

function renderSourceCards(text) {
  const urls = [...new Set((text.match(URL_RE) || []).map((url) => url.replace(/[）)。,，]+$/, "")))].slice(0, 8);
  if (!urls.length) return null;
  const list = el("section", "source-list");
  list.appendChild(el("h3", null, "來源"));
  urls.forEach((url) => {
    const link = el("a", "source-card", url);
    link.href = url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    try { link.appendChild(el("span", null, "來源日期：頁面未提供｜可信度：公開來源，建議開啟確認")); } catch { /* URL 保持原樣 */ }
    list.appendChild(link);
  });
  return list;
}

/* ── Markdown（極簡，不含使用者 HTML） ─────────────── */

function inline(s) {
  const frag = document.createDocumentFragment();
  for (const p of s.split(/(\*\*[^*]+\*\*|`[^`]+`)/g)) {
    if (!p) continue;
    if (p.startsWith("**") && p.endsWith("**")) frag.appendChild(el("strong", null, p.slice(2, -2)));
    else if (p.startsWith("`") && p.endsWith("`") && p.length > 2) frag.appendChild(el("code", null, p.slice(1, -1)));
    else frag.appendChild(document.createTextNode(p));
  }
  return frag;
}

function renderPlain(node, text) {
  let list = null;
  for (const line of text.split("\n")) {
    const m = line.match(/^\s*[-*•]\s+(.*)$/);
    if (m) {
      if (!list) {
        list = el("ul");
        node.appendChild(list);
      }
      const li = el("li");
      li.appendChild(inline(m[1]));
      list.appendChild(li);
      continue;
    }
    list = null;
    const p = el("div");
    if (!line.trim()) p.style.height = "8px";
    else p.appendChild(inline(line));
    node.appendChild(p);
  }
}

function splitFences(text) {
  const parts = [];
  const re = /```([a-zA-Z0-9_-]*)[ \t]*\n([\s\S]*?)```/g;
  let last = 0;
  let m;
  while ((m = re.exec(text))) {
    if (m.index > last) parts.push({ kind: "text", text: text.slice(last, m.index) });
    parts.push({ kind: "code", lang: (m[1] || "").toLowerCase(), text: m[2].replace(/\n$/, "") });
    last = m.index + m[0].length;
  }
  if (last < text.length) parts.push({ kind: "text", text: text.slice(last) });
  return parts;
}

function renderMessage(turn, text) {
  const box = el("div", "bubble-ai");
  const parts = splitFences(text);
  if (!parts.length) parts.push({ kind: "text", text });
  for (const part of parts) {
    if (part.kind === "code" && CARD_LANGS[part.lang]) {
      box.appendChild(renderSocCard(turn, part.lang, part.text));
      continue;
    }
    if (part.kind === "code") {
      const pre = el("pre", "code-block");
      pre.appendChild(el("code", null, part.text));
      box.appendChild(pre);
      continue;
    }
    const research = splitResearch(part.text);
    if (research) {
      if (research.leftover) {
        const extra = el("div");
        renderPlain(extra, research.leftover);
        box.appendChild(extra);
      }
      box.appendChild(renderResearch(turn, research));
      const sources = renderSourceCards(part.text);
      if (sources) box.appendChild(sources);
    } else {
      renderPlain(box, part.text);
    }
  }
  turn.appendChild(box);
  scrollChat();
}

/* ── 訊息區塊 ──────────────────────────────────────── */

function showPanel(name) {
  state.panel = name;
  $("home").hidden = name !== "home";
  $("guide-ig").hidden = name !== "ig";
  $("guide-research").hidden = name !== "research";
  $("chat").hidden = name !== "chat";
  $("composer").hidden = name === "ig" || name === "research";
  if (name === "home") loadHomeLists();
  if (name === "chat" || name === "home") $("input").focus();
}

function newTurn(prompt) {
  const t = el("div", "turn");
  if (prompt) t.dataset.prompt = prompt;
  t._artifacts = [];
  t._toolKeys = {};
  $("chat").appendChild(t);
  return t;
}

function addUser(text) {
  const t = newTurn(text);
  t.appendChild(el("div", "bubble-user", text));
  scrollChat();
  return t;
}

function classifyError(text, code) {
  const raw = String(code || "") + " " + String(text || "");
  if (/401|403|權限|授權/.test(raw)) return ["權限不足", "請確認授權狀態後再試。", "permission"];
  if (/缺少|待填|必填/.test(raw)) return ["缺少資料", "補上關鍵資料，或先改成草稿。", "missing"];
  if (/研究|來源|搜尋/.test(raw)) return ["外部研究失敗", "可以重試，或改用已知資料完成草稿。", "research"];
  if (/文件|下載|產出/.test(raw)) return ["文件產出失敗", "可以只重做失敗步驟。", "document"];
  if (/逾時|timeout/.test(raw)) return ["任務逾時", "可以只重做失敗步驟。", "timeout"];
  if (/網路|network|fetch|中斷/.test(raw)) return ["網路中斷", "連線恢復後可重試。", "network"];
  return ["AI 服務忙碌", "請稍後重試，或先改用簡單模式。", "busy"];
}

function addError(turn, text, retryFn, code) {
  const [title, guidance, kind] = classifyError(text, code);
  const box = el("div", "error");
  box.appendChild(el("strong", null, title));
  box.appendChild(el("div", null, guidance));
  const actions = el("div", "error-actions");
  if (retryFn) {
    const btn = el("button", "ghost", "重試");
    btn.type = "button";
    btn.addEventListener("click", retryFn);
    actions.appendChild(btn);
  }
  if (state.projectId && ["document", "timeout", "research"].includes(kind)) {
    const retryStep = el("button", "ghost", "只重做失敗步驟");
    retryStep.type = "button";
    retryStep.addEventListener("click", () => controlTask("retry", turn));
    actions.appendChild(retryStep);
  }
  if (["missing", "busy", "research"].includes(kind)) {
    const simple = el("button", "ghost", kind === "missing" ? "返回補資料" : "改用簡單模式");
    simple.type = "button";
    simple.addEventListener("click", () => {
      $("input").value = kind === "missing" ? "" : (turn.dataset.prompt || state.lastPrompt) + "\n請先產出最簡單、可修改的草稿；未知資料請標示待填。";
      showPanel("home");
      $("input").focus();
    });
    actions.appendChild(simple);
  }
  if (actions.childElementCount) box.appendChild(actions);
  turn.appendChild(box);
  scrollChat();
}

function getTimeline(turn) {
  let tl = turn.querySelector(".timeline");
  if (!tl) {
    tl = el("details", "timeline");
    tl.open = true;
    tl.appendChild(el("summary", "timeline-sum", "工作進度（尚在準備）"));
    turn.appendChild(tl);
  }
  return tl;
}

function progressLine(turn, key, icon, label, detail) {
  const tl = getTimeline(turn);
  let row = tl.querySelector('[data-step="' + CSS.escape(key) + '"]');
  if (!row) {
    row = el("div", "step running");
    row.dataset.step = key;
    row.dataset.status = "running";
    row.appendChild(el("span", "step-icon", icon));
    const body = el("div", "step-body");
    body.appendChild(el("b", null, label));
    row.appendChild(body);
    tl.appendChild(row);
  }
  const body = row.querySelector(".step-body");
  body.querySelector("b").textContent = label;
  let d = body.querySelector(".detail");
  if (detail) {
    if (!d) {
      d = el("span", "detail");
      body.appendChild(d);
    }
    d.textContent = detail;
  }
  scrollChat();
  return row;
}

function finishStep(turn, key, ok) {
  const row = turn.querySelector('[data-step="' + CSS.escape(key) + '"]');
  if (row) {
    row.className = "step " + (ok ? "ok" : "fail");
    row.dataset.status = ok ? "complete" : "failed";
  }
}

function pendingStep(turn, key, label, detail) {
  const row = progressLine(turn, key, "", label, detail);
  row.className = "step";
  row.dataset.status = "pending";
  return row;
}

function seedProgress(turn) {
  FLOW_STEPS.forEach(([key, label, detail]) => pendingStep(turn, key, label, detail));
  const summary = getTimeline(turn).querySelector("summary");
  summary.textContent = "工作進度（0 / " + FLOW_STEPS.length + " 完成）";
}

function updateTimelineSummary(turn) {
  const rows = [...turn.querySelectorAll(".timeline .step")];
  const done = rows.filter((row) => row.dataset.status === "complete").length;
  const summary = getTimeline(turn).querySelector("summary");
  summary.textContent = "工作進度（" + done + " / " + FLOW_STEPS.length + " 完成）";
}

function setFlowStep(turn, key, status, label, detail) {
  const row = progressLine(turn, key, "", label, detail);
  row.className = "step" + (status === "running" ? " running" : status === "complete" ? " ok" : status === "failed" ? " fail" : "");
  row.dataset.status = status;
  updateTimelineSummary(turn);
  return row;
}

function renderTaskControls(turn) {
  if (turn.querySelector(".task-controls")) return;
  const controls = el("div", "task-controls");
  const summary = el("button", null, "查看任務摘要");
  summary.type = "button";
  summary.addEventListener("click", openTaskSummary);
  const pause = el("button", null, "暫停任務");
  pause.type = "button";
  pause.addEventListener("click", () => controlTask("pause", turn));
  const retry = el("button", null, "重試失敗步驟");
  retry.type = "button";
  retry.addEventListener("click", () => controlTask("retry", turn));
  const stop = el("button", "danger", "停止生成");
  stop.type = "button";
  stop.addEventListener("click", () => controlTask("cancel", turn));
  controls.append(summary, pause, retry, stop);
  turn.appendChild(controls);
}

async function refreshProject() {
  if (!state.sessionId) return;
  try {
    const resp = await api("/api/session", { method: "POST", body: JSON.stringify({ session_id: state.sessionId }) });
    if (!resp.ok) return;
    const data = await resp.json();
    if (data.project_id) state.projectId = data.project_id;
  } catch (err) {
    if (err.message !== "needs-auth") return;
  }
}

async function controlTask(action, turn) {
  const label = { pause: "任務已暫停", retry: "正在準備重試失敗步驟", cancel: "已停止生成" }[action];
  if (action === "cancel" && state.abortController) state.abortController.abort();
  await refreshProject();
  if (!state.projectId) {
    if (action === "cancel") {
      setFlowStep(turn, "done", "failed", "已停止生成", "已停止等待回覆；已完成內容會保留。");
      announce(label);
    }
    return;
  }
  try {
    const resp = await api("/api/tasks/" + encodeURIComponent(state.projectId) + "/" + action, { method: "POST", body: "{}" });
    if (!resp.ok) throw new Error("task-control");
    const data = await resp.json();
    state.taskSummary = data;
    updateTaskDock(data);
    if (action === "pause") {
      if (state.abortController) state.abortController.abort();
      setFlowStep(turn, "done", "pending", "任務已暫停", "你可以在摘要中繼續，或輸入「接續剛才」。");
    } else if (action === "retry") {
      setFlowStep(turn, "repair", "running", "準備重試失敗步驟", "只會重做尚未完成的部分。");
      runTask("接續剛才，只重試失敗步驟。");
    } else {
      setFlowStep(turn, "done", "failed", "已停止生成", "已保留完成的內容與產出。");
    }
    announce(label);
  } catch (err) {
    addError(turn, "目前無法" + (action === "pause" ? "暫停" : action === "retry" ? "重試" : "停止") + "任務，請稍後再試。", null, "network");
  }
}

function updateTaskDock(summary) {
  const dock = $("task-dock");
  if (!summary) return;
  const content = $("task-dock-content");
  content.replaceChildren();
  content.appendChild(el("p", "muted", "狀態：" + ({ completed: "已完成", in_progress: "進行中", paused: "已暫停", failed: "需要處理", cancelled: "已停止" })[summary.workflow_status] || "處理中"));
  if (summary.current_step) content.appendChild(el("p", null, "目前：" + summary.current_step));
  if (summary.next_action) content.appendChild(el("p", "muted", "下一步：" + summary.next_action));
  dock.hidden = false;
}

function openTaskSummary() {
  const body = $("task-summary-body");
  body.replaceChildren();
  const summary = state.taskSummary;
  if (!summary) body.appendChild(el("p", "empty", "任務摘要還在建立中。"));
  else {
    body.appendChild(el("p", "modal-lede", "這是可理解的任務狀態，不包含 AI 的內部推理。"));
    const list = el("ol", "task-summary-list");
    (summary.steps || []).forEach((step) => {
      const status = { completed: "已完成", running: "執行中", failed: "需處理", pending: "等待中" }[step.status] || "等待中";
      list.appendChild(el("li", null, step.description + "（" + status + "）" + (step.note ? "：" + step.note : "")));
    });
    body.appendChild(list);
    if (summary.next_action) body.appendChild(el("p", "muted", "建議下一步：" + summary.next_action));
  }
  $("task-summary-sheet").hidden = false;
  trapFocus($("task-summary-sheet").querySelector(".modal-card"), closeTaskSummary);
}

function closeTaskSummary() {
  releaseTrap();
  $("task-summary-sheet").hidden = true;
}

function pushToolKey(turn, name, explicit) {
  state.toolSeq += 1;
  const key = explicit || "call-" + state.toolSeq + "-" + (name || "tool");
  if (!turn._toolKeys) turn._toolKeys = {};
  (turn._toolKeys[name] || (turn._toolKeys[name] = [])).push(key);
  return key;
}

function popToolKey(turn, name, explicit) {
  const q = turn._toolKeys && turn._toolKeys[name];
  if (explicit && q) {
    const i = q.indexOf(explicit);
    if (i >= 0) q.splice(i, 1);
    return explicit;
  }
  if (q && q.length) return q.shift();
  return explicit || "call-missing-" + (name || "tool");
}

function addArtifact(turn, art) {
  turn._artifacts = turn._artifacts || [];
  turn._artifacts.push(art);
  const ext = (art.filename || "").split(".").pop().toLowerCase();
  const card = el("details", "artifact");
  card.dataset.kind = ["docx", "pptx", "xlsx", "pdf", "md"].includes(ext) ? "document" : "artifact";
  card.open = true;
  const sum = el("summary");
  sum.appendChild(el("div", "icon", FILE_ICONS[ext] || "▪"));
  const meta = el("div", "meta");
  meta.appendChild(el("div", "name", art.filename || "產出檔"));
  const bits = [];
  if (art.version > 1) bits.push("第 " + art.version + " 版");
  if (art.verified === true) bits.push("已通過檢查");
  if (art.verified === false) bits.push("檢查有警告");
  if (art.drive_url) bits.push("已上傳雲端");
  if (bits.length) meta.appendChild(el("div", "where", bits.join(" · ")));
  sum.appendChild(meta);
  card.appendChild(sum);

  const body = el("div", "artifact-body");
  body.appendChild(el("p", "muted", "完成度：已產出草稿。請檢查所有「待填」欄位後再使用。"));
  if (art.warning) body.appendChild(el("div", "warn", art.warning));
  const actions = el("div", "card-actions");
  if (art.artifact_id) {
    const a = el("a", null, "下載");
    a.href = "/api/download?artifact_id=" + encodeURIComponent(art.artifact_id);
    actions.appendChild(a);
  }
  if (art.drive_url) {
    const a = el("a", null, "開雲端");
    a.href = art.drive_url;
    a.target = "_blank";
    a.rel = "noopener";
    actions.appendChild(a);
  }
  const retry = el("button", null, "重試");
  retry.type = "button";
  retry.addEventListener("click", () => {
    const prompt = turn.dataset.prompt || state.lastPrompt;
    if (prompt) send(prompt);
  });
  actions.appendChild(retry);
  body.appendChild(actions);
  body.appendChild(followUpBar("請依照這份產出繼續處理：" + (art.filename || "產出檔"), "document"));
  card.appendChild(body);
  turn.appendChild(card);
  turn.querySelectorAll("[data-card]").forEach((c) => c._refreshDl && c._refreshDl());
  scrollChat();
}

function renderCompletionActions(turn) {
  if (turn.querySelector(".completion-actions")) return;
  const wrap = el("section", "next-actions completion-actions");
  wrap.appendChild(el("h3", null, "下一步"));
  const actions = el("div", "card-actions");
  [
    ["修改這份", "請修改剛才的產出，先詢問只會影響結果的關鍵資訊。"],
    ["補充資料", "我要補充剛才任務的資料："],
    ["儲存到任務", "請整理剛才產出的任務摘要與下一步。"],
    ["開始新任務", ""],
  ].forEach(([label, prompt]) => {
    const btn = el("button", null, label);
    btn.type = "button";
    btn.addEventListener("click", () => prompt ? startFollowUp(prompt) : newChat());
    actions.appendChild(btn);
  });
  wrap.appendChild(actions);
  turn.appendChild(wrap);
}

function renderBanner(lines) {
  const node = $("banner");
  node.replaceChildren();
  lines.forEach((line, i) => {
    if (i) node.appendChild(document.createElement("br"));
    const s = String(line);
    let last = 0;
    s.replace(URL_RE, (url, offset) => {
      if (offset > last) node.appendChild(document.createTextNode(s.slice(last, offset)));
      if (/^https?:\/\//i.test(url)) {
        const a = el("a", null, url);
        a.href = url;
        a.target = "_blank";
        a.rel = "noopener noreferrer";
        node.appendChild(a);
      } else {
        node.appendChild(document.createTextNode(url));
      }
      last = offset + url.length;
      return url;
    });
    if (last < s.length) node.appendChild(document.createTextNode(s.slice(last)));
  });
  node.hidden = !lines.length;
}

/* ── 授權入口 ──────────────────────────────────────── */

function showGate(message, locked) {
  $("gate").hidden = false;
  $("app").hidden = true;
  closeSettings();
  const err = $("gate-error");
  if (message) {
    err.textContent = message;
    err.hidden = false;
    err.dataset.locked = locked ? "1" : "0";
  } else {
    err.textContent = "";
    err.hidden = true;
    err.dataset.locked = "0";
  }
  $("gate-token").focus();
}

$("gate-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const btn = $("gate-form").querySelector("button[type=submit]");
  btn.disabled = true;
  const err = $("gate-error");
  err.hidden = true;
  try {
    const resp = await fetch("/api/auth", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token: $("gate-token").value }),
    });
    if (resp.ok) {
      $("gate-token").value = "";
      $("gate").hidden = true;
      $("app").hidden = false;
      init();
      return;
    }
    const detail = await readDetail(resp);
    if (resp.status === 429) {
      showGate(detail || "嘗試次數過多，請稍後再試", true);
    } else {
      showGate(detail || "授權未通過，請再試一次。", false);
    }
  } catch {
    showGate(BUSY_TEXT, false);
  } finally {
    btn.disabled = false;
  }
});

/* ── 送出與 SSE ────────────────────────────────────── */

function selectedAnswer(question) {
  return question.answer || question.default || "待填";
}

function hasAny(text, values) {
  return values.some((value) => text.includes(value));
}

function isComplete(text) {
  const concrete = /\d{1,2}[月\/\-]\d{1,2}|\d{1,2}[：:]\d{2}|\d{4}[\-\/]\d{1,2}|https?:\/\//.test(text);
  return text.length >= 42 || concrete;
}

function buildPreflight(text) {
  const clean = text.trim();
  const decided = /你決定|你來決定|自行決定|都可以/.test(clean);
  const kind = hasAny(clean, ["網宣", "貼文", "輪播", "限動", "Reels", "招生"]) ? "social"
    : hasAny(clean, ["活動", "茶會", "社課", "演講", "營隊"]) ? "event"
    : hasAny(clean, ["研究", "比較", "其他學校"]) ? "research"
    : hasAny(clean, ["企劃書", "文件", "簡報", "表單", "預算表"]) ? "document" : "general";
  const questions = [];
  const add = (id, title, options, fallback) => questions.push({ id, title, options, default: fallback, answer: decided ? fallback : "" });
  if (!decided && !isComplete(clean)) {
    if (kind === "social") {
      if (!hasAny(clean, ["招生", "宣傳", "通知", "社員", "招募"])) add("goal", "這次網宣主要目標是什麼？", ["招生", "活動宣傳", "社課通知", "社員經營", "其他"], "活動宣傳");
      if (!/時間|地點|待填|日期/.test(clean)) add("schedule", "目前有確定時間與地點嗎？", ["已確定", "尚未確定，先標示待填", "我稍後補充"], "尚未確定，先標示待填");
    } else if (kind === "event") {
      if (!hasAny(clean, ["茶會", "社課", "演講", "營隊", "工作坊"])) add("format", "這次要規劃哪一種活動？", ["期初茶會", "社課", "演講", "營隊", "其他"], "期初茶會");
      if (!/日期|時間|地點|待填/.test(clean)) add("schedule", "活動時間與地點目前的狀態？", ["已確定", "尚未確定，先標示待填", "我稍後補充"], "尚未確定，先標示待填");
    } else if (kind === "document") {
      if (!hasAny(clean, ["企劃書", "簡報", "表單", "預算", "會議紀錄", "文件"])) add("format", "想先完成哪一種文件？", ["活動企劃書", "簡報", "表單", "預算表", "會議紀錄"], "活動企劃書");
    } else if (kind === "research") {
      if (!hasAny(clean, ["招生", "茶會", "社課", "宣傳", "活動"])) add("focus", "你最想比較哪一個面向？", ["招生", "活動宣傳", "社課經營", "社群內容", "其他"], "活動宣傳");
    }
  }
  return { text: clean, kind, decided, questions: questions.slice(0, 3) };
}

function taskKindName(kind) {
  return ({ social: "網宣草稿", event: "活動計畫", research: "研究整理", document: "文件草稿", general: "AI 任務" })[kind] || "AI 任務";
}

function renderPreflight() {
  const draft = state.preflight;
  if (!draft) return;
  const body = $("preflight-body");
  body.replaceChildren();
  const intro = el("p", "modal-lede", draft.questions.length ? "在開始前，還需要確認 " + draft.questions.length + " 件事。只問會影響結果的內容。" : "需求已足夠開始。請確認摘要後再執行。");
  body.appendChild(intro);
  if (draft.decided) body.appendChild(el("p", "banner", "你選擇讓 AI 決定；以下會採用合理預設，未確認資料會標示為「待填」。"));
  draft.questions.forEach((question) => {
    const box = el("section", "preflight-question");
    box.appendChild(el("h3", null, question.title));
    const opts = el("div", "preflight-options");
    question.options.forEach((option) => {
      const btn = el("button", null, option);
      btn.type = "button";
      btn.setAttribute("aria-pressed", String(selectedAnswer(question) === option));
      btn.addEventListener("click", () => { question.answer = option; renderPreflight(); });
      opts.appendChild(btn);
    });
    box.appendChild(opts);
    const label = el("label", null, "補充說明（選填）");
    const input = el("input");
    input.type = "text";
    input.value = question.note || "";
    input.placeholder = "例如：週三晚上、商管大樓待確認";
    input.addEventListener("input", () => { question.note = input.value; });
    label.appendChild(input);
    box.appendChild(label);
    body.appendChild(box);
  });
  const summary = el("section", "preflight-question");
  summary.appendChild(el("h3", null, "任務摘要"));
  summary.appendChild(el("p", "muted", "要做：「" + taskKindName(draft.kind) + "」\n需求：「" + draft.text + "」"));
  body.appendChild(summary);
  $("preflight-title").textContent = draft.questions.length ? "在開始前，還需要確認 " + draft.questions.length + " 件事" : "確認後開始";
}

function openPreflight(text) {
  const clean = (text || $("input").value).trim();
  if (!clean || state.busy) return;
  state.preflight = buildPreflight(clean);
  $("preflight-sheet").hidden = false;
  renderPreflight();
  trapFocus($("preflight-sheet").querySelector(".modal-card"), closePreflight);
}

function closePreflight() {
  releaseTrap();
  $("preflight-sheet").hidden = true;
  $("input").focus();
}

function preflightPrompt(draft) {
  const answers = draft.questions.map((q) => "- " + q.title + "：" + selectedAnswer(q) + (q.note ? "（補充：" + q.note + "）" : "")).join("\n");
  return draft.text + "\n\n【已確認的任務摘要】\n任務類型：" + taskKindName(draft.kind) + "\n" + (answers || "- 使用者確認需求已完整") + "\n請依此執行；未確認的日期、地點或姓名請清楚標示「待填」，不要自行杜撰。";
}

function confirmPreflight() {
  const draft = state.preflight;
  if (!draft) return;
  closePreflight();
  $("input").value = "";
  $("input").style.height = "auto";
  runTask(preflightPrompt(draft));
}

function skipPreflight() {
  if (!state.preflight) return;
  state.preflight.questions.forEach((q) => { if (!q.answer) q.answer = "先標示待填"; });
  confirmPreflight();
}

function send(text) { openPreflight(text); }

async function ensureSession() {
  if (state.sessionId) return state.sessionId;
  const resp = await api("/api/session", { method: "POST", body: JSON.stringify({}) });
  if (!resp.ok) throw new Error("session");
  const data = await resp.json();
  state.sessionId = data.session_id;
  return state.sessionId;
}

async function runTask(text) {
  text = (text || $("input").value).trim();
  if (!text || state.busy) return;

  state.busy = true;
  state.lastPrompt = text;
  $("send").disabled = true;
  showPanel("chat");
  addUser(text);
  $("input").value = "";
  $("input").style.height = "auto";
  announce("處理中");

  const turn = newTurn(text);
  state.activeTurn = turn;
  state.taskSummary = null;
  state.projectId = null;
  seedProgress(turn);
  setFlowStep(turn, "understand", "running", "已理解需求", "正在整理目標與預期成果。");
  renderTaskControls(turn);
  state.abortController = new AbortController();

  const retry = () => runTask(text);

  try {
    const sid = await ensureSession();
    const resp = await api("/api/chat", {
      method: "POST",
      signal: state.abortController.signal,
      body: JSON.stringify({
        session_id: sid,
        message: text,
        destination: $("destination").value,
        model: $("model").value || null,
      }),
    });
    if (resp.status === 429) {
      const detail = await readDetail(resp);
      addError(turn, detail || "嘗試次數過多，請稍後再試", retry);
      announce(detail || "請稍後再試");
      return;
    }
    if (!resp.ok || !resp.body) {
      addError(turn, BUSY_TEXT, retry);
      announce(BUSY_TEXT);
      return;
    }

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const parts = buffer.split("\n\n");
      buffer = parts.pop() || "";
      for (const part of parts) {
        const line = part.trim();
        if (!line.startsWith("data:")) continue;
        let ev;
        try {
          ev = JSON.parse(line.slice(5).trim());
        } catch {
          continue;
        }
        handleEvent(turn, ev, retry);
      }
    }
  } catch (err) {
    if (err.name === "AbortError") {
      announce("已停止等待回覆");
      if (state.activeTurn) setFlowStep(turn, "done", "failed", "已停止生成", "已保留完成的內容。");
      return;
    }
    if (err.message !== "needs-auth") {
      addError(turn, BUSY_TEXT, retry, "busy");
      announce(BUSY_TEXT);
    }
  } finally {
    turn.querySelectorAll(".step.running").forEach((n) => n.classList.remove("running"));
    state.busy = false;
    state.abortController = null;
    state.activeTurn = null;
    $("send").disabled = false;
    $("input").focus();
    loadHomeLists();
  }
}

function handleEvent(turn, ev, retry) {
  const callId = ev.tool_call_id || ev.call_id || ev.id || "";
  switch (ev.type) {
    case "session":
      if (ev.session_id) state.sessionId = ev.session_id;
      break;

    case "step": {
      const key = ev.key || callId || "step-" + (ev.label || ev.text || state.toolSeq);
      progressLine(turn, key, "◇", ev.label || ev.text || "進行中…", ev.detail);
      if (ev.status === "ok" || ev.ok === true || ev.done) finishStep(turn, key, true);
      if (ev.status === "fail" || ev.ok === false) finishStep(turn, key, false);
      announce(ev.label || "進行中");
      break;
    }

    case "task_understood": {
      const produces = (ev.produces || []).join("、");
      setFlowStep(turn, "understand", "complete", "已理解需求", (ev.skill || "任務") + (produces ? "；預計產出：" + produces : ""));
      setFlowStep(turn, "facts", "running", "確認必要資料", "正在檢查已知資料與待填欄位。");
      refreshProject();
      break;
    }

    case "plan_created": {
      state.taskSummary = { steps: ev.step_details || (ev.steps || []).map((description) => ({ description, status: "pending" })), workflow_status: "in_progress", next_action: "等待必要資料確認後繼續" };
      updateTaskDock(state.taskSummary);
      if (ev.missing_facts && ev.missing_facts.length) {
        setFlowStep(turn, "facts", "complete", "確認必要資料", "未設定「" + ev.missing_facts.join("、") + "」，產出會標示待填。");
      } else {
        setFlowStep(turn, "facts", "complete", "確認必要資料", "必要資料已確認，繼續查詢相關內容。");
      }
      break;
    }

    case "retrieval_started":
      setFlowStep(turn, "research", "running", "查詢社團資料", "正在整理與任務相關的資料。");
      break;

    case "retrieval_result":
      setFlowStep(turn, "research", "complete", "查詢社團資料", "已找到 " + (ev.count || 0) + " 段可用參考。");
      break;

    case "step_started":
      setFlowStep(turn, "draft", "running", "產生初稿", "正在完成下一個必要步驟。");
      break;

    case "step_failed":
      setFlowStep(turn, "draft", "failed", "產生初稿需要處理", "可只重做失敗步驟，或改成簡單草稿。");
      break;

    case "research_sources_saved":
      progressLine(turn, "research-sources", "⌕", "已保存研究來源", "之後產出可以回到來源檢查");
      finishStep(turn, "research-sources", true);
      break;

    case "tool_started": {
      const phase = TOOL_PHASES[ev.name] || ["draft", "產生初稿"];
      setFlowStep(turn, phase[0], "running", phase[1], "正在處理這個步驟。");
      break;
    }

    case "tool_completed": {
      const phase = TOOL_PHASES[ev.name] || ["draft", "產生初稿"];
      setFlowStep(turn, phase[0], ev.ok === false ? "failed" : "complete", phase[1], ev.ok === false ? "這一步沒有完成，可重試。" : "這個步驟已完成。");
      break;
    }

    case "verification_started":
      setFlowStep(turn, "verify", "running", "檢查內容", "正在檢查產出是否可用。");
      break;

    case "verification_result":
      setFlowStep(turn, "verify", ev.ok ? "complete" : "failed", "檢查內容", ev.ok ? "檢查完成。" : "發現需要修正的內容。" );
      break;

    case "repair_started":
      setFlowStep(turn, "repair", "running", "修正問題", "正在修正可自動處理的內容。");
      break;

    case "artifact":
    case "artifact_ready":
      finishStep(turn, "repair", true);
      addArtifact(turn, ev);
      break;

    case "message":
      if (ev.text) renderMessage(turn, ev.text);
      break;

    case "task_completed":
      state.taskSummary = ev.summary || state.taskSummary;
      updateTaskDock(state.taskSummary);
      setFlowStep(turn, "done", "complete", "產出完成", "你可以修改、轉換或開始新任務。");
      renderCompletionActions(turn);
      announce("完成");
      break;

    case "task_paused":
      progressLine(turn, "workflow-status", "Ⅱ", "任務已暫停", "可按繼續，或說「接續剛才」");
      finishStep(turn, "workflow-status", true);
      break;

    case "task_resumed":
      progressLine(turn, "workflow-status", "▶", "任務已恢復", ev.summary && ev.summary.next_action ? ev.summary.next_action : "");
      finishStep(turn, "workflow-status", true);
      break;

    case "task_retry_ready":
      progressLine(turn, "workflow-status", "↻", "已準備重試失敗步驟", "說「接續剛才」開始");
      finishStep(turn, "workflow-status", true);
      break;

    case "task_cancelled":
      progressLine(turn, "workflow-status", "■", "任務已取消", "已保留已完成的步驟");
      finishStep(turn, "workflow-status", true);
      break;

    case "task_failed":
      state.taskSummary = ev.summary || state.taskSummary;
      updateTaskDock(state.taskSummary);
      setFlowStep(turn, "done", "failed", "任務有一步需要處理", "可只重做失敗步驟，已完成內容會保留。");
      break;

    case "done":
      turn.querySelectorAll(".step.running").forEach((n) => n.classList.remove("running"));
      announce("完成");
      break;

    case "error":
      addError(turn, friendlyError(ev.text), retry);
      announce(friendlyError(ev.text));
      break;
  }
}

/* ── 首頁清單 ──────────────────────────────────────── */

function formatWhen(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const diff = (Date.now() - d.getTime()) / 1000;
  if (diff < 60) return "剛剛";
  if (diff < 3600) return Math.floor(diff / 60) + " 分鐘前";
  if (diff < 86400) return Math.floor(diff / 3600) + " 小時前";
  return d.toLocaleDateString("zh-Hant", { month: "numeric", day: "numeric" });
}

function fillList(node, items, emptyText, render) {
  node.replaceChildren();
  if (!items.length) {
    node.appendChild(el("p", "empty", emptyText));
    return;
  }
  items.forEach((item) => node.appendChild(render(item)));
}

async function continueSession(sid, title) {
  closeSessionSheet();
  try {
    const resp = await api("/api/session", { method: "POST", body: JSON.stringify({ session_id: sid }) });
    if (!resp.ok) {
      announce(BUSY_TEXT);
      return;
    }
    const data = await resp.json();
    state.sessionId = data.session_id;
    showPanel("chat");
    $("chat").replaceChildren();
    const note = el("p", "empty", "繼續「" + (title || "先前的任務") + "」。直接輸入下一步即可。");
    $("chat").appendChild(note);
    $("input").focus();
  } catch (err) {
    if (err.message !== "needs-auth") announce(BUSY_TEXT);
  }
}

async function loadHomeLists() {
  try {
    const [sResp, aResp] = await Promise.all([
      api("/api/sessions").catch(() => null),
      api("/api/artifacts").catch(() => null),
    ]);
    if (sResp && sResp.ok) {
      const data = await sResp.json();
      const sessions = (data.sessions || []).slice(0, 5);
      fillList($("recent-sessions"), sessions, "還沒有任務", (s) => {
        const btn = el("button", "list-row");
        btn.type = "button";
        btn.append(el("span", "name", s.title || "未命名任務"), el("span", "when", formatWhen(s.updated_at)));
        btn.addEventListener("click", () => continueSession(s.session_id, s.title));
        return btn;
      });
    }
    if (aResp && aResp.ok) {
      const data = await aResp.json();
      const arts = (data.artifacts || []).slice(0, 5);
      fillList($("recent-artifacts"), arts, "還沒有產出", (a) => {
        if (a.artifact_id) {
          const link = el("a", "list-row");
          link.href = "/api/download?artifact_id=" + encodeURIComponent(a.artifact_id);
          link.append(el("span", "name", a.filename || "檔案"), el("span", "when", formatWhen(a.created_at) || "下載"));
          return link;
        }
        const row = el("div", "list-row");
        row.append(el("span", "name", a.filename || "檔案"), el("span", "when", formatWhen(a.created_at)));
        return row;
      });
    }
  } catch (err) {
    if (err.message !== "needs-auth") {
      /* 清單失敗不鎖頁 */
    }
  }
}

function openSessionSheet() {
  const sheet = $("session-sheet");
  sheet.hidden = false;
  trapFocus(sheet.querySelector(".modal-card"), closeSessionSheet);
  loadSessionSheet();
}

function closeSessionSheet() {
  releaseTrap();
  $("session-sheet").hidden = true;
}

async function loadSessionSheet() {
  const box = $("session-list");
  box.replaceChildren(el("p", "empty", "讀取中…"));
  try {
    const resp = await api("/api/sessions");
    if (!resp.ok) {
      box.replaceChildren(el("p", "empty", BUSY_TEXT));
      return;
    }
    const data = await resp.json();
    const sessions = data.sessions || [];
    fillList(box, sessions, "還沒有可繼續的任務", (s) => {
      const btn = el("button", "list-row");
      btn.type = "button";
      btn.append(el("span", "name", s.title || "未命名任務"), el("span", "when", formatWhen(s.updated_at)));
      btn.addEventListener("click", () => continueSession(s.session_id, s.title));
      return btn;
    });
  } catch (err) {
    if (err.message !== "needs-auth") box.replaceChildren(el("p", "empty", BUSY_TEXT));
  }
}

/* ── 引導 ──────────────────────────────────────────── */

function igPrompt(goal, platform, need) {
  const fence = PLATFORM_FENCE[platform] || "ig-post";
  return [
    "請為淡江大學領袖禪學社撰寫網宣草稿（僅草稿，不要發布）。",
    "",
    "目標：" + goal,
    "平台：" + platform,
    "需求：" + need,
    "",
    "請用 " + fence + " 程式碼區塊輸出完整草稿。",
    "若是輪播，每一頁用 --- 分隔。",
    "文案要生活化、貼近大學生，從對方處境開講。時間地點未確認就寫待填。",
    "不要療效宣稱，不要宗教招募感。",
  ].join("\n");
}

function researchPrompt(school, need) {
  const scope = school.trim() ? school.trim() : "全部找得到的公開他校資料";
  return [
    "請研究其他學校禪學社或類似社團的公開網宣做法，整理給淡江大學領袖禪學社內部參考。",
    "",
    "比較範圍：" + scope,
    "想了解：" + need,
    "",
    "請務必用這四個標題分段：",
    "## 其他學校怎麼做",
    "## 為什麼可能有效",
    "## 淡江可以怎麼改良",
    "## 哪些內容不能直接照抄",
    "",
    "僅供內部參考，不要直接複製他校文案。",
  ].join("\n");
}

$("quick-actions").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-action]");
  if (!btn) return;
  const action = btn.dataset.action;
  if (action === "continue") openSessionSheet();
  else {
    const quickPrompts = {
      ig: "幫我做一份網宣草稿，時間與地點未確認請標示待填。",
      research: "幫我研究其他學校的公開社群做法，整理可供淡江參考的方向。",
      ...PROMPTS,
    };
    if (!quickPrompts[action]) return;
    $("input").value = quickPrompts[action];
    $("input").focus();
    $("input").dispatchEvent(new Event("input"));
  }
});

$("suggestions").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-prompt]");
  if (!btn) return;
  $("input").value = btn.dataset.prompt;
  $("input").dispatchEvent(new Event("input"));
  $("input").focus();
});

document.querySelectorAll(".mode-chip").forEach((btn) => {
  btn.addEventListener("click", () => {
    state.mode = btn.dataset.mode || "ask";
    document.querySelectorAll(".mode-chip").forEach((chip) => {
      const active = chip === btn;
      chip.classList.toggle("is-active", active);
      chip.setAttribute("aria-pressed", String(active));
    });
    $("mode-hint").textContent = MODE_HINTS[state.mode] || MODE_HINTS.ask;
    $("input").focus();
  });
});

document.querySelectorAll("[data-back]").forEach((btn) => {
  btn.addEventListener("click", () => showPanel(btn.dataset.back || "home"));
});

$("ig-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const goal = ($("ig-form").querySelector("input[name=ig-goal]:checked") || {}).value || "招生";
  const platform = ($("ig-form").querySelector("input[name=ig-platform]:checked") || {}).value || "貼文";
  const need = $("ig-need").value.trim();
  if (!need) return;
  send(igPrompt(goal, platform, need));
});

$("research-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const need = $("research-need").value.trim();
  if (!need) return;
  send(researchPrompt($("research-school").value, need));
});

/* ── 焦點陷阱 ──────────────────────────────────────── */

let trapCleanup = null;

function isVisible(n) {
  return !!(n.offsetWidth || n.offsetHeight || n.getClientRects().length);
}

function focusables(root) {
  return [...root.querySelectorAll('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])')].filter(
    (n) => !n.disabled && n.getAttribute("aria-hidden") !== "true" && isVisible(n)
  );
}

function trapFocus(root, onClose) {
  releaseTrap();
  const nodes = focusables(root);
  (nodes[0] || root).focus();
  const onKey = (e) => {
    if (e.key === "Escape") {
      e.preventDefault();
      onClose();
      return;
    }
    if (e.key !== "Tab") return;
    const list = focusables(root);
    if (!list.length) return;
    const first = list[0];
    const last = list[list.length - 1];
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first.focus();
    }
  };
  document.addEventListener("keydown", onKey);
  trapCleanup = () => document.removeEventListener("keydown", onKey);
}

function releaseTrap() {
  if (trapCleanup) trapCleanup();
  trapCleanup = null;
}

/* ── 設定抽屜 ──────────────────────────────────────── */

function openSettings() {
  $("settings").hidden = false;
  $("settings-backdrop").hidden = false;
  trapFocus($("settings"), closeSettings);
}

function closeSettings() {
  releaseTrap();
  $("settings").hidden = true;
  $("settings-backdrop").hidden = true;
  $("settings-btn").focus();
}

$("settings-btn").addEventListener("click", openSettings);
$("settings-close").addEventListener("click", closeSettings);
$("settings-backdrop").addEventListener("click", closeSettings);

$("logout-btn").addEventListener("click", async () => {
  const hint = $("logout-hint");
  hint.hidden = true;
  try {
    const resp = await fetch("/api/auth/logout", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    if (missingStatus(resp)) {
      closeSettings();
      showGate("已清除本機狀態。請重新整理頁面。");
      return;
    }
    closeSettings();
    showGate();
  } catch {
    closeSettings();
    showGate("已清除本機狀態。請重新整理頁面。");
  }
});

function setAdminTools(show, status) {
  $("admin-tools").hidden = !show;
  if (status) $("admin-status").textContent = status;
  $("admin-form").hidden = !!show && state.adminEndpoint !== false;
  if (state.adminEndpoint === false) $("admin-form").hidden = true;
}

$("admin-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const err = $("admin-error");
  err.hidden = true;
  const token = $("admin-token").value;
  try {
    const resp = await fetch("/api/admin/auth", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token }),
    });
    if (missingStatus(resp)) {
      state.adminEndpoint = false;
      err.hidden = false;
      err.textContent = "後端尚未支援";
      setAdminTools(true, "後端尚未支援獨立管理碼，仍可編輯本學期資料。");
      return;
    }
    state.adminEndpoint = true;
    if (resp.status === 429) {
      err.hidden = false;
      err.textContent = (await readDetail(resp)) || "嘗試次數過多，請稍後再試";
      return;
    }
    if (!resp.ok) {
      err.hidden = false;
      err.textContent = (await readDetail(resp)) || "管理碼不正確";
      return;
    }
    state.isAdmin = true;
    $("admin-token").value = "";
    setAdminTools(true, "已進入管理。");
  } catch {
    err.hidden = false;
    err.textContent = BUSY_TEXT;
  }
});

$("admin-logout-btn").addEventListener("click", async () => {
  try {
    const resp = await fetch("/api/admin/logout", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
    if (!missingStatus(resp) && !resp.ok && resp.status !== 401) {
      $("admin-error").hidden = false;
      $("admin-error").textContent = BUSY_TEXT;
      return;
    }
  } catch {
    /* 舊後端沒有此端點時仍關閉管理區 */
  }
  state.isAdmin = false;
  setAdminTools(false, "輸入管理碼後可編輯本學期資料。");
});

/* ── 本學期設定 ────────────────────────────────────── */

async function openTerm() {
  try {
    const resp = await api("/api/term");
    if (!resp.ok) {
      announce(BUSY_TEXT);
      return;
    }
    const data = await resp.json();
    const form = $("term-form");
    form.replaceChildren();
    for (const f of data.fields || []) {
      const wrap = el("label", "term-field");
      const head = el("span", "term-label");
      head.appendChild(document.createTextNode(f.label));
      if (!f.known) head.appendChild(el("em", "unset", "未設定"));
      wrap.appendChild(head);
      const control = f.kind === "longtext" || f.kind === "list" ? el("textarea") : el("input");
      control.name = f.key;
      control.value = f.value || "";
      control.placeholder = f.hint || "";
      if (control.tagName === "TEXTAREA") control.rows = f.kind === "list" ? 4 : 2;
      wrap.appendChild(control);
      form.appendChild(wrap);
    }
    $("term-status").textContent = data.updated_at ? "最後更新：" + data.updated_at : "尚未設定過";
    $("term-modal").hidden = false;
    trapFocus($("term-modal").querySelector(".modal-card"), closeTerm);
  } catch (err) {
    if (err.message !== "needs-auth") announce(BUSY_TEXT);
  }
}

function closeTerm() {
  releaseTrap();
  $("term-modal").hidden = true;
}

async function saveTerm() {
  const fields = {};
  for (const c of $("term-form").querySelectorAll("input, textarea")) fields[c.name] = c.value;
  try {
    const resp = await api("/api/term", { method: "POST", body: JSON.stringify({ fields }) });
    if (!resp.ok) {
      announce(BUSY_TEXT);
      return;
    }
    const data = await resp.json();
    closeTerm();
    if (data.term_label) $("term-label").textContent = data.term_label;
  } catch (err) {
    if (err.message !== "needs-auth") announce(BUSY_TEXT);
  }
}

$("term-btn").addEventListener("click", openTerm);
$("term-save").addEventListener("click", saveTerm);
$("term-close").addEventListener("click", closeTerm);
$("term-cancel").addEventListener("click", closeTerm);
$("term-modal").addEventListener("click", (e) => {
  if (e.target.id === "term-modal") closeTerm();
});
$("session-close").addEventListener("click", closeSessionSheet);
$("session-sheet").addEventListener("click", (e) => {
  if (e.target.id === "session-sheet") closeSessionSheet();
});

/* ── 新任務 ────────────────────────────────────────── */

async function newChat() {
  const old = state.sessionId;
  try {
    if (old) {
      const resp = await api("/api/reset", { method: "POST", body: JSON.stringify({ session_id: old }) });
      if (!resp.ok && resp.status !== 404) {
        const turn = $("chat").hidden ? null : newTurn();
        const host = turn || $("home");
        const box = el("div", "error");
        box.appendChild(document.createTextNode("新任務沒有建立成功。"));
        const actions = el("div", "error-actions");
        const btn = el("button", "ghost", "重試");
        btn.type = "button";
        btn.addEventListener("click", newChat);
        actions.appendChild(btn);
        box.appendChild(actions);
        host.prepend ? host.prepend(box) : host.appendChild(box);
        announce("新任務失敗");
        return;
      }
    }
    state.sessionId = null;
    state.projectId = null;
    state.taskSummary = null;
    $("task-dock").hidden = true;
    $("chat").replaceChildren();
    showPanel("home");
    announce("已開始新任務");
  } catch (err) {
    if (err.message === "needs-auth") return;
    announce("新任務失敗");
    const box = el("div", "error");
    box.appendChild(document.createTextNode("新任務沒有建立成功。"));
    const actions = el("div", "error-actions");
    const btn = el("button", "ghost", "重試");
    btn.type = "button";
    btn.addEventListener("click", newChat);
    actions.appendChild(btn);
    box.appendChild(actions);
    ($("home").hidden ? $("chat") : $("home")).prepend(box);
  }
}

$("reset").addEventListener("click", newChat);

$("send").addEventListener("click", () => send());
$("clear-input").addEventListener("click", () => {
  $("input").value = "";
  $("input").style.height = "auto";
  $("input").focus();
  announce("已清除輸入內容");
});
$("attachment").addEventListener("change", async () => {
  const file = $("attachment").files && $("attachment").files[0];
  $("attachment").value = "";
  if (!file) return;
  if (file.size > 50000) { announce("文件超過 50KB，請先擷取需要的段落再加入。"); return; }
  try {
    const text = (await file.text()).trim();
    if (!text) { announce("這份文件沒有可加入的文字。"); return; }
    $("input").value = ($("input").value + "\n\n【加入的文件：「" + file.name + "」】\n" + text).trim();
    $("input").dispatchEvent(new Event("input"));
    announce("已加入文件文字；送出前可再修改。");
  } catch { announce("無法讀取這份文件，請改貼上需要的文字。"); }
});
$("voice-input").addEventListener("click", () => {
  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!Recognition) { announce("這個瀏覽器目前不支援語音輸入，請改用文字輸入。"); return; }
  if (state.voice) { state.voice.stop(); return; }
  const recognition = new Recognition();
  state.voice = recognition;
  recognition.lang = "zh-TW";
  recognition.interimResults = true;
  recognition.continuous = false;
  const base = $("input").value;
  $("voice-input").textContent = "停止";
  recognition.onresult = (event) => {
    const words = [...event.results].map((result) => result[0].transcript).join("");
    $("input").value = base + words;
    $("input").dispatchEvent(new Event("input"));
  };
  recognition.onerror = () => announce("語音輸入沒有完成，請改用文字補充。");
  recognition.onend = () => { state.voice = null; $("voice-input").textContent = "語音"; $("input").focus(); };
  recognition.start();
  announce("正在聆聽，說完後會填入輸入框。");
});
$("input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
    e.preventDefault();
    send();
  }
});
$("input").addEventListener("input", () => {
  $("input").style.height = "auto";
  $("input").style.height = Math.min($("input").scrollHeight, 160) + "px";
});

$("preflight-close").addEventListener("click", closePreflight);
$("preflight-back").addEventListener("click", closePreflight);
$("preflight-skip").addEventListener("click", skipPreflight);
$("preflight-confirm").addEventListener("click", confirmPreflight);
$("preflight-sheet").addEventListener("click", (e) => { if (e.target.id === "preflight-sheet") closePreflight(); });
$("task-summary-close").addEventListener("click", closeTaskSummary);
$("task-summary-sheet").addEventListener("click", (e) => { if (e.target.id === "task-summary-sheet") closeTaskSummary(); });
$("task-dock-summary").addEventListener("click", openTaskSummary);

document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  if (!$("preflight-sheet").hidden) closePreflight();
  else if (!$("task-summary-sheet").hidden) closeTaskSummary();
  else if (!$("term-modal").hidden) closeTerm();
  else if (!$("session-sheet").hidden) closeSessionSheet();
  else if (!$("settings").hidden) closeSettings();
});

/* ── Instagram 狀態 / 啟動 ─────────────────────────── */

async function loadIgStatus() {
  const bar = $("draft-bar");
  bar.hidden = false;
  try {
    const resp = await fetch("/api/instagram/status");
    if (missingStatus(resp) || !resp.ok) return;
    const data = await resp.json();
    if (data.connected && data.mode && data.mode !== "draft") bar.hidden = true;
  } catch {
    bar.hidden = false;
  }
}

function fillModels(h) {
  const sel = $("model");
  sel.replaceChildren();
  const models = h.models || [];
  for (const m of models) {
    const o = el("option", null, String(m).split("/").pop());
    o.value = m;
    if (m === h.model) o.selected = true;
    sel.appendChild(o);
  }
  if (h.model && !models.includes(h.model)) {
    const o = el("option", null, String(h.model).split("/").pop());
    o.value = h.model;
    o.selected = true;
    sel.insertBefore(o, sel.firstChild);
  }
}

async function init() {
  applyViewport();
  try {
    const resp = await api("/api/health");
    if (!resp.ok) {
      renderBanner([BUSY_TEXT]);
      return;
    }
    const h = await resp.json();
    state.health = h;
    fillModels(h);
    $("destination").value = h.destination || "local";
    if (h.term && h.term.label) $("term-label").textContent = h.term.label;
    const notices = [];
    if (h.term && h.term.configured === false) {
      notices.push("本學期資料還沒填。產出裡的時間地點可能會是「待填」，可到設定的管理入口補上。");
    }
    renderBanner(notices);
    await loadIgStatus();
    await loadHomeLists();
    if (state.isAdmin) setAdminTools(true, "已進入管理。");
    showPanel("home");
    $("input").focus();
  } catch (err) {
    if (err.message === "needs-auth") return;
    renderBanner([BUSY_TEXT]);
  }
}

(async function boot() {
  applyViewport();
  window.visualViewport?.addEventListener("resize", applyViewport);
  window.visualViewport?.addEventListener("scroll", applyViewport);
  window.addEventListener("resize", applyViewport);
  try {
    const status = await (await fetch("/api/auth")).json();
    state.isAdmin = !!status.is_admin;
    if (status.mode === "token" && !status.authenticated) {
      showGate();
    } else {
      $("app").hidden = false;
      if (state.isAdmin) setAdminTools(true, "已進入管理。");
      init();
    }
  } catch {
    $("app").hidden = false;
    init();
  }
})();
