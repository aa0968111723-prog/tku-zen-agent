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
  ["facts", "已確認活動資料", "未確定欄位會清楚標示待填"],
  ["research", "查找社團參考", "只使用與任務相關的資料"],
  ["draft", "建立內容", "先完成可修改的版本"],
  ["verify", "最後檢查", "檢查待填資訊與內容品質"],
];
const TOOL_LABELS = {
  create_social_post: "建立社群貼文",
  create_social_carousel: "建立 IG 輪播",
  create_social_story: "建立 IG 限時動態",
  create_reels_script: "建立 Reels 腳本",
  retrieval: "查找社團資料",
  verification: "檢查內容",
  repair: "自動修正",
  artifact: "產出檔案",
};
const CARD_MARKS = { "ig-post": "IG", "ig-carousel": "輪", "ig-story": "限", "reels-script": "▶", "content-calendar": "曆", "ab-test": "AB", "image-prompt": "圖", "video-prompt": "影" };
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
  attachments: [],
  voice: null,
  orbTimer: null,
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

function setOrb(status, label) {
  const orb = $("ai-orb");
  if (!orb) return;
  const names = {
    idle: "等待中",
    thinking: "正在思考",
    asking: "需要你確認",
    complete: "任務完成",
    error: "需要處理",
  };
  const text = label || names[status] || names.idle;
  orb.className = "ai-orb is-" + (status || "idle");
  orb.setAttribute("aria-label", "禪光 AI：" + text);
  $("ai-orb-label").textContent = text;
  if (state.orbTimer) clearTimeout(state.orbTimer);
  if (status === "complete") {
    state.orbTimer = setTimeout(() => setOrb("idle"), 1600);
  }
}

function humanLabel(value, fallback = "處理任務") {
  const raw = String(value || "").trim();
  if (!raw) return fallback;
  if (TOOL_LABELS[raw]) return TOOL_LABELS[raw];
  let text = raw;
  Object.entries(TOOL_LABELS).forEach(([internal, label]) => { text = text.replaceAll(internal, label); });
  return text.replace(/[_-]+/g, " ");
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

function visualPrompt(raw) {
  return (
    "請根據以下繁體中文社群內容，產生一張適合淡江大學社團使用的社群視覺草稿。"
    + "風格現代、安靜、溫暖、專業；使用淡江藍、米白與柔和灰，保留足夠留白。"
    + "圖片中的文字必須少且清楚；沒有明確日期、地點或網址時，不要自行捏造。\n\n"
    + String(raw || "").slice(0, 6000)
  );
}

async function generateVisual(raw, host, button) {
  button.disabled = true;
  button.textContent = "正在生成視覺稿…";
  const result = el("section", "visual-result");
  result.setAttribute("aria-live", "polite");
  result.appendChild(el("p", "muted", "正在以 fal.ai 生成視覺草稿，完成後可直接下載。"));
  host.appendChild(result);
  try {
    const resp = await api("/api/visual/generate", {
      method: "POST",
      body: JSON.stringify({ prompt: visualPrompt(raw) }),
    });
    if (!resp.ok) {
      const detail = await readDetail(resp);
      throw new Error(detail || "視覺服務暫時無法使用，請稍後重試。");
    }
    const data = await resp.json();
    const image = (data.images || [])[0];
    if (!image || !image.url) throw new Error("視覺稿沒有取得可用圖片，請調整內容後再試。");
    result.replaceChildren();
    result.appendChild(el("h4", null, "視覺草稿"));
    const preview = document.createElement("img");
    preview.src = image.url;
    preview.alt = "由 AI 生成的社群視覺草稿";
    preview.loading = "lazy";
    result.appendChild(preview);
    result.appendChild(el("p", "muted", "已由 fal.ai 生成；請檢查文字與資訊正確後再使用。"));
    const actions = el("div", "card-actions");
    const open = el("a", null, "開啟並下載");
    open.href = image.url;
    open.target = "_blank";
    open.rel = "noopener";
    open.download = "社群視覺草稿.jpg";
    actions.appendChild(open);
    result.appendChild(actions);
    announce("視覺稿已完成");
  } catch (err) {
    result.replaceChildren();
    result.appendChild(el("strong", null, "視覺稿尚未完成"));
    result.appendChild(el("p", "muted", friendlyError(err.message)));
    const retry = el("button", "ghost", "重試生成視覺稿");
    retry.type = "button";
    retry.addEventListener("click", () => generateVisual(raw, host, retry));
    result.appendChild(retry);
  } finally {
    button.disabled = false;
    button.textContent = "生成視覺稿";
  }
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
  if (kind === "social") {
    const visual = el("button", null, "生成視覺稿");
    visual.type = "button";
    visual.addEventListener("click", () => generateVisual(raw, wrap, visual));
    actions.appendChild(visual);
  }
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
  const stage = el("div", "carousel-stage");
  const body = el("div", "soc-body carousel-page");
  body.tabIndex = 0;
  body.setAttribute("role", "group");
  const ind = el("span", "page-ind");
  const show = () => {
    body.textContent = pages[idx] || "";
    const count = pages.length || 1;
    ind.textContent = "第 " + (idx + 1) + " 頁／共 " + count + " 頁";
    body.setAttribute("aria-label", ind.textContent);
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
  body.addEventListener("keydown", (e) => {
    if (e.key === "ArrowLeft") { e.preventDefault(); prev.click(); }
    if (e.key === "ArrowRight") { e.preventDefault(); next.click(); }
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
  const pageActions = el("div", "page-actions");
  [["修改單頁", "請只修改輪播第 "], ["重新產生單頁", "請只重新產生輪播第 "]].forEach(([label, prefix]) => {
    const button = el("button", null, label);
    button.type = "button";
    button.addEventListener("click", () => startFollowUp(prefix + (idx + 1) + " 頁，其餘頁面保持不變。\n目前內容：\n" + (pages[idx] || "")));
    pageActions.appendChild(button);
  });
  stage.append(body, meta, pageActions);
  card.appendChild(stage);
  show();
}

function renderInstagramPost(card, raw) {
  const preview = el("div", "ig-preview");
  const head = el("div", "ig-preview-head");
  head.append(el("span", "ig-avatar", "禪"), el("strong", null, "tku_zen"), el("span", "ig-more", "•••"));
  const canvas = el("div", "ig-post-canvas");
  canvas.append(el("span", "ig-mark", "禪"), el("b", null, "領袖禪學社"), el("small", null, "貼文視覺預覽"));
  const caption = el("div", "soc-body ig-caption", raw);
  preview.append(head, canvas, caption);
  card.appendChild(preview);
}

function renderStory(card, raw) {
  const preview = el("div", "story-preview");
  preview.setAttribute("aria-label", "IG 限時動態 9 比 16 尺寸預覽");
  preview.append(el("span", "story-kicker", "領袖禪學社"), el("div", "story-copy", raw), el("span", "story-cta", "查看活動詳情 ↑"));
  card.appendChild(preview);
}

function renderReels(card, raw) {
  const timeline = el("ol", "reels-timeline");
  const lines = raw.split("\n").map((line) => line.trim()).filter(Boolean).slice(0, 12);
  (lines.length ? lines : [raw]).forEach((line, index) => {
    const match = line.match(/(?:\[)?(\d{1,2}(?::\d{2})?(?:\s*[-–~至]\s*\d{1,2}(?::\d{2})?)?\s*(?:秒|s)?)(?:\])?[：:]?\s*(.*)/i);
    const item = el("li");
    item.append(el("time", null, match ? match[1] : String(index * 3).padStart(2, "0") + " 秒"), el("p", null, match ? (match[2] || line) : line));
    timeline.appendChild(item);
  });
  card.appendChild(timeline);
}

function renderSocCard(turn, lang, raw) {
  const card = el("article", "soc-card");
  card.dataset.card = "1";
  card.dataset.kind = lang;
  card.setAttribute("aria-label", CARD_LANGS[lang] || "社群內容預覽");
  const head = el("div", "card-head");
  head.append(el("div", "card-kind", CARD_MARKS[lang] || "稿"));
  head.append(el("div", "card-title", CARD_LANGS[lang] || lang));
  card.appendChild(head);
  if (lang === "ig-carousel") {
    renderCarousel(card, splitCarousel(raw));
  } else if (lang === "ig-post") {
    renderInstagramPost(card, raw);
  } else if (lang === "ig-story") {
    renderStory(card, raw);
  } else if (lang === "reels-script") {
    renderReels(card, raw);
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

function renderTextSourceLinks(text) {
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
  if (!String(text).includes("\n") && String(text).includes("\\n")) text = String(text).replaceAll("\\n", "\n");
  turn._lastContent = String(text || "");
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
      const sources = renderTextSourceLinks(part.text);
      if (sources) box.appendChild(sources);
    } else {
      renderPlain(box, part.text);
    }
  }
  turn.appendChild(box);
  scrollChat();
}

/* ── 研究驗證卡片（反問／來源卡／污染警示） ─────────── */

const SRC_STATUS = {
  verified: { cls: "st-verified", label: "已驗證" },
  partially_verified: { cls: "st-partial", label: "部分驗證" },
  inferred: { cls: "st-inferred", label: "AI 推測" },
  stale: { cls: "st-stale", label: "資料過期" },
  conflicted: { cls: "st-conflict", label: "來源衝突" },
  wrong_entity: { cls: "st-conflict", label: "研究對象錯誤" },
  insufficient_evidence: { cls: "st-stale", label: "證據不足" },
};

const RESEARCH_CHIP = {
  complete: "st-verified",
  partially_verified: "st-partial",
  unverified: "st-partial",
  no_reliable_source: "st-stale",
  needs_clarification: "st-inferred",
  blocked: "st-conflict",
  internal: "st-inferred",
};

function sendOption(text) {
  if (!text) return;
  if (text.endsWith("@") || text.endsWith("：") || text.endsWith(":")) {
    // 需要使用者補資料（例如 IG 帳號）——放進輸入框讓他接著打
    $("input").value = text;
    $("input").focus();
    $("input").dispatchEvent(new Event("input"));
    return;
  }
  runTask(text);
}

function renderClarification(turn, ev) {
  const card = el("article", "clarify-card");
  card.appendChild(el("h3", null, ev.school ? "【你指的是哪個社團？】" : "【請確認研究對象】"));
  card.appendChild(el("p", "clarify-q", ev.question || ""));

  let topic = "";
  const opts = el("div", "clarify-options");
  (ev.options || []).forEach((o) => {
    const b = el("button", null, o.label);
    b.type = "button";
    b.addEventListener("click", () => {
      let text = o.send_text || o.label;
      if (topic && !text.endsWith("@")) text += "想了解的主題是：" + topic + "。";
      sendOption(text);
    });
    opts.appendChild(b);
  });

  if (ev.topic_question && (ev.topic_options || []).length) {
    card.appendChild(el("p", "clarify-q sub", "【" + ev.topic_question + "】（可先選，再點上面的對象）"));
    const chips = el("div", "topic-chips");
    (ev.topic_options || []).forEach((t) => {
      const c = el("button", "chip", t);
      c.type = "button";
      c.addEventListener("click", () => {
        topic = topic === t ? "" : t;
        chips.querySelectorAll(".chip").forEach((n) => n.classList.toggle("on", n.textContent === topic));
      });
      chips.appendChild(c);
    });
    card.appendChild(chips);
  }
  card.appendChild(opts);
  turn.appendChild(card);
  scrollChat();
}

function renderSourceCards(turn, ev) {
  const cards = ev.cards || [];
  const old = turn.querySelector(".sources-wrap");
  if (old) old.remove();
  const wrap = el("details", "sources-wrap");
  wrap.open = cards.length > 0;
  wrap.appendChild(el("summary", "timeline-sum", cards.length ? "來源卡（" + cards.length + "）" : "來源卡（無可驗證來源）"));

  const bar = el("div", "src-filter");
  const filters = [
    ["all", "全部"],
    ["verified", "只看已驗證"],
    ["inferred", "查看推測內容"],
  ];
  let active = "all";
  const apply = () => {
    wrap.querySelectorAll(".source-card").forEach((c) => {
      const st = c.dataset.status || "";
      c.hidden =
        (active === "verified" && st !== "verified") ||
        (active === "inferred" && st !== "inferred" && st !== "partially_verified");
    });
    bar.querySelectorAll("button").forEach((b) => b.classList.toggle("on", b.dataset.f === active));
  };
  filters.forEach(([f, label]) => {
    const b = el("button", "chip", label);
    b.type = "button";
    b.dataset.f = f;
    b.addEventListener("click", () => {
      active = f;
      apply();
    });
    bar.appendChild(b);
  });
  wrap.appendChild(bar);

  if (!cards.length) {
    wrap.appendChild(el("p", "empty", "這次研究沒有任何可驗證來源，因此不提供確定結論。"));
  }

  cards.forEach((c) => {
    const st = SRC_STATUS[c.status] || SRC_STATUS.insufficient_evidence;
    const card = el("article", "source-card");
    card.dataset.status = c.status || "";
    const head = el("div", "src-head");
    head.appendChild(el("span", "src-badge " + st.cls, c.status_label || st.label));
    const who = c.source_scope === "external"
      ? (c.organization || c.school || "外校來源")
      : "淡江內部" + (c.academic_term ? "（" + c.academic_term + "）" : "");
    head.appendChild(el("b", null, who));
    card.appendChild(head);
    card.appendChild(el("div", "src-title", c.title || ""));
    const bits = [];
    if (c.school && c.source_scope === "external") bits.push("學校：" + c.school);
    if (c.captured_at) bits.push("來源日期：" + c.captured_at);
    else if (c.published_at) bits.push("來源日期：" + c.published_at);
    if (c.source_scope === "internal" && !c.is_current) bits.push("歷史資料，非本學期事實");
    if (bits.length) card.appendChild(el("div", "src-meta", bits.join("　·　")));
    if (c.excerpt) {
      const d = el("details", "src-excerpt");
      d.appendChild(el("summary", null, "查看原文"));
      d.appendChild(el("div", "body", c.excerpt));
      card.appendChild(d);
    }
    const actions = el("div", "card-actions");
    if (c.url) {
      const a = el("a", null, "開啟來源");
      a.href = c.url;
      a.target = "_blank";
      a.rel = "noopener noreferrer";
      actions.appendChild(a);
    }
    const report = el("button", null, "回報來源不正確");
    report.type = "button";
    report.addEventListener("click", () =>
      runTask("來源卡「" + (c.title || c.source_id) + "」的內容或歸屬有誤，請重新查證並更正。")
    );
    actions.appendChild(report);
    if (c.source_scope === "external" && (c.school || c.organization)) {
      const re = el("button", null, "重新查詢官方來源");
      re.type = "button";
      re.addEventListener("click", () =>
        runTask("請只用官方公開來源，重新查詢" + (c.organization || c.school) + "的最新公開資料。")
      );
      actions.appendChild(re);
    }
    card.appendChild(actions);
    wrap.appendChild(card);
  });
  turn.appendChild(wrap);
  scrollChat();
}

function renderContamination(turn, ev) {
  const card = el("article", "contamination");
  card.appendChild(el("h3", null, "【疑似資料歸屬錯誤】"));
  card.appendChild(el("p", null, ev.message || "目前內容可能混入淡江內部資料，不能視為外校公開資料。"));
  (ev.items || []).slice(0, 4).forEach((i) => {
    const msg = (i.message || "").replace(/[。．]\s*$/, "");
    card.appendChild(el("div", "cont-item", msg + (i.sentence ? "——「" + i.sentence + "」" : "")));
  });
  const actions = el("div", "card-actions");
  (ev.actions || []).forEach((a) => {
    const b = el("button", null, a.label);
    b.type = "button";
    b.addEventListener("click", () => sendOption(a.send_text));
    actions.appendChild(b);
  });
  card.appendChild(actions);
  turn.appendChild(card);
  scrollChat();
}

function renderResearchStatus(turn, ev) {
  let chip = turn.querySelector(".research-chip");
  if (!chip) {
    chip = el("div", "research-chip");
    turn.appendChild(chip);
  }
  chip.className = "research-chip " + (RESEARCH_CHIP[ev.status] || "st-stale");
  chip.textContent = "研究狀態：" + (ev.label || ev.status || "");
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

function renderUnderstandingCard(turn, draft) {
  if (!draft || turn.querySelector(".understanding-card")) return;
  const card = el("article", "work-card understanding-card");
  card.setAttribute("aria-labelledby", "understanding-" + state.toolSeq);
  card.appendChild(el("p", "card-eyebrow", "任務理解"));
  const title = el("h2", null, draft.text);
  title.id = "understanding-" + state.toolSeq;
  card.appendChild(title);
  card.appendChild(el("p", "card-lede", "我會先依已確認條件整理資料，完成後提供預覽、版本與可直接操作的下一步。"));
  const chips = el("div", "summary-chips");
  chips.append(el("span", null, taskKindName(draft.kind)));
  draft.questions.filter((q) => q.answer || q.skipped).slice(0, 3).forEach((q) => chips.append(el("span", null, q.skipped ? "待填" : selectedAnswer(q))));
  card.appendChild(chips);
  turn.appendChild(card);
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
  if (/vision|visual|圖片理解|視覺服務/i.test(raw)) return ["AI 視覺服務暫時無法使用", "可改用文字描述圖片內容，或稍後重試。", "visual"];
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
  if (["missing", "busy", "research", "visual"].includes(kind)) {
    const simple = el("button", "ghost", kind === "missing" ? "返回補資料" : kind === "visual" ? "改用文字描述" : "改用簡單模式");
    simple.type = "button";
    simple.addEventListener("click", () => {
      if (kind === "visual") {
        clearAttachments();
        $("input").value = (turn.dataset.prompt || state.lastPrompt) + "\n請先依我的文字描述完成；我會再補充圖片中的內容。";
      } else {
        $("input").value = kind === "missing" ? "" : (turn.dataset.prompt || state.lastPrompt) + "\n請先產出最簡單、可修改的草稿；未知資料請標示待填。";
      }
      showPanel("home");
      $("input").focus();
    });
    actions.appendChild(simple);
  }
  if (actions.childElementCount) box.appendChild(actions);
  turn.appendChild(box);
  setOrb("error");
  scrollChat();
}

function getTimeline(turn) {
  let tl = turn.querySelector(".timeline");
  if (!tl) {
    tl = el("details", "timeline work-card execution-card");
    const summary = el("summary", "timeline-sum");
    const heading = el("span", "timeline-heading");
    heading.append(el("b", "timeline-title", "正在準備任務"), el("span", "timeline-live", "尚在準備"));
    summary.append(heading, el("strong", "timeline-count", "0／" + FLOW_STEPS.length));
    tl.appendChild(summary);
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
  updateTimelineSummary(turn);
}

function updateTimelineSummary(turn) {
  const rows = [...turn.querySelectorAll(".timeline .step")];
  const done = rows.filter((row) => row.dataset.status === "complete").length;
  const running = rows.find((row) => row.dataset.status === "running");
  const timeline = getTimeline(turn);
  timeline.querySelector(".timeline-count").textContent = done + "／" + FLOW_STEPS.length;
  timeline.querySelector(".timeline-live").textContent = running ? running.querySelector("b").textContent : done === FLOW_STEPS.length ? "全部完成" : "等待下一步";
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
  const retry = el("button", null, "只重試失敗步驟");
  retry.type = "button";
  retry.addEventListener("click", () => controlTask("retry", turn));
  const stop = el("button", "danger", "停止生成");
  stop.type = "button";
  stop.addEventListener("click", () => controlTask("cancel", turn));
  controls.append(summary, pause, retry, stop);
  getTimeline(turn).appendChild(controls);
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
  if (action === "cancel") {
    // 先請伺服器停止（釋放 session 執行鎖與模型呼叫），再中斷本地連線
    requestStreamCancel();
    if (state.abortController) state.abortController.abort();
  }
  await refreshProject();
  if (!state.projectId) {
    if (action === "cancel") {
      setFlowStep(turn, "verify", "failed", "已停止生成", "已停止等待回覆；已完成內容會保留。");
      getTimeline(turn).querySelector(".timeline-title").textContent = "任務已停止";
      setOrb("idle", "已停止");
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
      setFlowStep(turn, "verify", "pending", "任務已暫停", "你可以在摘要中繼續，或輸入「接續剛才」。");
      getTimeline(turn).querySelector(".timeline-title").textContent = "任務已暫停";
      setOrb("idle", "任務已暫停");
    } else if (action === "retry") {
      setFlowStep(turn, "draft", "running", "準備重試失敗步驟", "只會重做尚未完成的部分。");
      runTask("接續剛才，只重試失敗步驟。");
    } else {
      setFlowStep(turn, "verify", "failed", "已停止生成", "已保留完成的內容與產出。");
      getTimeline(turn).querySelector(".timeline-title").textContent = "任務已停止";
      setOrb("idle", "已停止");
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
  if (state.preflight) body.appendChild(renderMiniSummary(state.preflight, true));
  if (!summary && !state.preflight) body.appendChild(el("p", "empty", "任務摘要還在建立中。"));
  if (summary) {
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

function stableFilename(filename) {
  const raw = String(filename || "產出檔").trim();
  const dot = raw.lastIndexOf(".");
  let stem = dot > 0 ? raw.slice(0, dot) : raw;
  const ext = dot > 0 ? raw.slice(dot).toLowerCase() : "";
  stem = stem
    .replace(/(?:_\d+){2,}$/i, "")
    .replace(/(?:[_\- ]+(?:final|修正版|草稿版?))+(?:[_\- ]*\d+)*$/gi, "")
    .replace(/^(\d{3})[_\- ]*(上|下)[_\- ]*/u, (_, year, term) => year + "-" + (term === "上" ? "1" : "2") + "-")
    .replace(/[_\s]+/g, "-")
    .replace(/-+/g, "-")
    .replace(/^-|-$/g, "");
  return (stem || "產出檔") + ext;
}

function artifactTitle(filename) {
  return stableFilename(filename).replace(/\.[^.]+$/, "").replace(/^\d{3}-[12]-/, "").replace(/-/g, " ");
}

function versionLabel(version) {
  const n = Number(version || 1);
  if (n <= 1) return "草稿版";
  if (n === 2) return "修正版";
  return "最終版";
}

function openTextPreview(title, raw, href) {
  const modal = el("div", "modal preview-modal");
  const panel = el("section", "modal-card preview-modal-card");
  panel.setAttribute("role", "dialog");
  panel.setAttribute("aria-modal", "true");
  const head = el("header", "modal-head");
  head.appendChild(el("h2", null, title));
  const close = el("button", "icon-btn", "×");
  close.type = "button";
  close.setAttribute("aria-label", "關閉完整預覽");
  head.appendChild(close);
  const body = el("div", "full-preview");
  if (raw) body.textContent = raw;
  else body.appendChild(el("p", "empty", "此格式請下載後開啟完整內容。"));
  const foot = el("footer", "modal-foot");
  if (href) {
    const link = el("a", "primary", "下載檔案");
    link.href = href;
    foot.appendChild(link);
  }
  const done = el("button", "ghost", "完成");
  done.type = "button";
  foot.appendChild(done);
  panel.append(head, body, foot);
  modal.appendChild(panel);
  document.body.appendChild(modal);
  const dismiss = () => { releaseTrap(); modal.remove(); };
  close.addEventListener("click", dismiss);
  done.addEventListener("click", dismiss);
  modal.addEventListener("click", (e) => { if (e.target === modal) dismiss(); });
  trapFocus(panel, dismiss);
}

function addArtifact(turn, art) {
  turn._artifacts = turn._artifacts || [];
  turn._artifacts.push(art);
  const filename = stableFilename(art.filename);
  const ext = filename.split(".").pop().toLowerCase();
  const raw = art.preview || turn._lastContent || "";
  const card = el("article", "artifact work-card result-card");
  card.dataset.kind = ["docx", "pptx", "xlsx", "pdf", "md"].includes(ext) ? "document" : "artifact";
  card.dataset.verified = art.verified === false ? "warn" : "ok";
  card.setAttribute("aria-label", "產出結果：" + artifactTitle(filename));
  const sum = el("header", "result-head");
  sum.appendChild(el("div", "icon", FILE_ICONS[ext] || "▪"));
  const meta = el("div", "meta");
  meta.appendChild(el("p", "card-eyebrow", "產出結果"));
  meta.appendChild(el("h2", "name", artifactTitle(filename)));
  sum.appendChild(meta);
  card.appendChild(sum);

  const body = el("div", "artifact-body");
  const facts = el("dl", "result-facts");
  [["狀態", art.verified === false ? "！檢查有警告" : "✓ 已通過檢查"], ["版本", versionLabel(art.version)], ["格式", CARD_LANGS[art.format] || ({ md: "Markdown", docx: "企劃文件", pptx: "簡報", xlsx: "試算表", pdf: "PDF" })[ext] || ext.toUpperCase()]].forEach(([term, value]) => {
    facts.append(el("dt", null, term), el("dd", null, value));
  });
  body.appendChild(facts);
  body.appendChild(el("p", "filename-stable", "檔名：" + filename));
  const preview = el("section", "artifact-preview");
  preview.appendChild(el("h3", null, "內容預覽"));
  const previewText = raw.split("\n").map((line) => line.trim()).filter(Boolean).slice(0, 5).join("\n");
  preview.appendChild(el("p", null, previewText || "檔案已建立，可開啟完整預覽或下載查看。"));
  body.appendChild(preview);
  if (art.warning) body.appendChild(el("div", "warn", art.warning));
  const actions = el("div", "card-actions");
  const href = art.artifact_id ? "/api/download?artifact_id=" + encodeURIComponent(art.artifact_id) : "";
  const open = el("button", "primary-action", "開啟完整預覽");
  open.type = "button";
  open.addEventListener("click", () => openTextPreview(artifactTitle(filename), raw, href));
  actions.appendChild(open);
  const copy = el("button", null, "複製內容");
  copy.type = "button";
  copy.addEventListener("click", async () => { copy.textContent = await copyText(raw || filename) ? "已複製" : "複製失敗"; });
  actions.appendChild(copy);
  if (art.artifact_id) {
    const a = el("a", null, "下載檔案");
    a.href = href;
    actions.appendChild(a);
  }
  if (art.drive_url) {
    const a = el("a", null, "開雲端");
    a.href = art.drive_url;
    a.target = "_blank";
    a.rel = "noopener";
    actions.appendChild(a);
  }
  [["修改這份", "請修改上一份產出，只調整我接下來指定的內容。"], ["重新產生", "請依相同條件重新產生上一份內容。"], ["轉成輪播", "請把上一份內容轉成 IG 輪播，每頁重點單一。"], ["轉成 Reels", "請把上一份內容轉成 Reels 腳本與時間軸。"], ["產生簡報", "請把上一份內容轉成簡報。"]].forEach(([label, prompt]) => {
    const button = el("button", null, label);
    button.type = "button";
    button.addEventListener("click", () => startFollowUp(prompt));
    actions.appendChild(button);
  });
  body.appendChild(actions);
  card.appendChild(body);
  turn.appendChild(card);
  turn.querySelectorAll("[data-card]").forEach((c) => c._refreshDl && c._refreshDl());
  scrollChat();
}

function renderCompletionActions(turn) {
  if (turn.querySelector(".completion-actions")) return;
  const wrap = el("section", "next-actions completion-actions work-card");
  wrap.appendChild(el("p", "card-eyebrow", "下一步操作"));
  wrap.appendChild(el("h3", null, "接下來你可以"));
  const actions = el("div", "card-actions");
  [
    ["補上時間與地點", "我要補上剛才任務的時間與地點："],
    ["修改文案語氣", "請調整剛才內容的語氣："],
    ["轉成 IG 輪播", "請把上一份內容轉成 IG 輪播。"],
    ["產生簡報", "請把上一份內容轉成簡報。"],
    ["建立活動待辦", "請依上一份任務建立活動待辦。"],
    ["儲存到本學期資料", "請整理剛才已確認、適合存入本學期資料的內容。"],
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
  if (question.skipped) return "待填";
  if (Array.isArray(question.answer)) return question.answer.length ? question.answer.join("、") : (question.default || "待填");
  return question.other || question.answer || question.default || "待填";
}

function hasQuestionAnswer(question) {
  return !!(question.skipped || question.other || (Array.isArray(question.answer) ? question.answer.length : question.answer));
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
  const add = (id, title, options, fallback, type = "single") => questions.push({ id, title, options, default: fallback, type, answer: decided ? fallback : "", other: "", skipped: false });
  if (!decided && !isComplete(clean)) {
    if (kind === "social") {
      if (!hasAny(clean, ["貼文", "輪播", "限動", "Reels"])) add("platform", "想先做哪一種社群內容？", ["IG 貼文", "IG 輪播", "IG 限時動態", "Reels"], "IG 貼文", "platform");
      if (!hasAny(clean, ["招生", "宣傳", "通知", "社員", "招募"])) add("goal", "這次網宣主要目標是什麼？", ["招生", "活動宣傳", "社課通知", "社員經營"], "活動宣傳");
      if (!hasAny(clean, ["新生", "幹部", "全校", "老師", "社員"])) add("audience", "這份內容主要給誰看？", ["社團幹部", "新生", "全校學生", "指導老師"], "全校學生", "people");
      if (!hasAny(clean, ["親切", "活潑", "正式", "溫暖", "幽默"])) add("tone", "希望文案呈現什麼語氣？", ["自然親切", "青春活潑", "沉穩可信", "簡潔直接"], "自然親切", "tone");
      if (!/時間|地點|待填|日期/.test(clean)) add("schedule", "目前有確定時間與地點嗎？", ["已確定", "尚未確定，先標示待填", "我稍後補充"], "尚未確定，先標示待填");
    } else if (kind === "event") {
      if (!hasAny(clean, ["茶會", "社課", "演講", "營隊", "工作坊"])) add("format", "這次要規劃哪一種活動？", ["期初茶會", "社課", "演講", "營隊"], "期初茶會");
      if (!hasAny(clean, ["新生", "幹部", "全校", "老師", "社員"])) add("audience", "這場活動主要邀請誰？", ["新生", "全校學生", "社團社員", "社團幹部"], "新生", "people");
      if (!/日期|時間|地點|待填/.test(clean)) add("schedule", "活動時間與地點目前的狀態？", ["已確定", "尚未確定，先標示待填", "我稍後補充"], "尚未確定，先標示待填");
    } else if (kind === "document") {
      if (!hasAny(clean, ["企劃書", "簡報", "表單", "預算", "會議紀錄", "文件"])) add("format", "想先完成哪一種文件？", ["活動企劃書", "簡報", "表單", "預算表", "會議紀錄"], "活動企劃書");
    } else if (kind === "research") {
      if (!hasAny(clean, ["招生", "茶會", "社課", "宣傳", "活動"])) add("focus", "你最想比較哪一個面向？", ["招生", "活動宣傳", "社課經營", "社群內容", "其他"], "活動宣傳");
      add("schools", "想優先比較哪些學校類型？", ["北部大學", "私立大學", "領袖社", "禪學社"], "領袖社、禪學社", "multi");
    } else {
      add("goal", "這次想先完成什麼？", ["規劃活動", "製作網宣", "產生企劃書", "整理會議紀錄"], "產生企劃書");
    }
  }
  return { text: clean, kind, decided, questions: questions.slice(0, 3), current: 0, stage: questions.length ? "questions" : "summary" };
}

function taskKindName(kind) {
  return ({ social: "網宣草稿", event: "活動計畫", research: "研究整理", document: "文件草稿", general: "AI 任務" })[kind] || "AI 任務";
}

function preflightSummaryRows(draft) {
  const answer = (id) => {
    const question = draft.questions.find((q) => q.id === id);
    if (!question) return "";
    if (draft.stage === "questions" && !hasQuestionAnswer(question)) return "等待回答";
    return selectedAnswer(question);
  };
  const knownOutput = (["Reels", "IG 輪播", "輪播", "限動", "簡報", "企劃書", "表單", "預算表", "會議紀錄", "IG 貼文", "貼文"].find((value) => draft.text.includes(value)) || "").replace(/^IG /, "IG ");
  const knownType = ["期初茶會", "茶會", "社課", "演講", "營隊", "工作坊", "招生"].find((value) => draft.text.includes(value)) || "";
  const knownAudience = ["淡江大學新生", "新生", "社團幹部", "全校學生", "指導老師", "社員"].find((value) => draft.text.includes(value)) || "";
  const output = knownOutput || answer("platform") || answer("format") || (draft.decided ? "企劃書" : taskKindName(draft.kind));
  const type = knownType || answer("goal") || (draft.kind === "event" ? (answer("format") || "活動規劃") : draft.decided && draft.kind === "general" ? "內容規劃" : taskKindName(draft.kind));
  const schedule = answer("schedule");
  const scheduleQuestion = draft.questions.find((q) => q.id === "schedule");
  const details = (scheduleQuestion && scheduleQuestion.details) || {};
  return [
    ["任務", draft.text],
    ["活動類型", type],
    ["目標對象", knownAudience || answer("audience") || "待填"],
    ["時間", details.date || details.time ? [details.date, details.time].filter(Boolean).join(" ") : schedule && schedule.includes("已確定") && !schedule.includes("尚未") ? "依需求內容" : "待填"],
    ["地點", details.location || (schedule && schedule.includes("已確定") && !schedule.includes("尚未") ? "依需求內容" : "待填")],
    ["輸出格式", output],
    ["資料來源", draft.kind === "research" ? "公開參考、社團知識庫" : "社團知識庫、歷年範例"],
  ];
}

function renderMiniSummary(draft, full = false) {
  const section = el("section", full ? "task-confirm-card" : "live-summary");
  section.appendChild(el("h3", null, full ? "任務摘要" : "摘要即時更新"));
  const list = el("dl", "summary-grid");
  preflightSummaryRows(draft).forEach(([key, value]) => {
    if (!full && ["時間", "地點", "資料來源"].includes(key)) return;
    list.append(el("dt", null, key), el("dd", null, value || "待填"));
  });
  section.appendChild(list);
  return section;
}

function renderQuestionControl(question) {
  const wrap = el("div", "question-control");
  const choiceTypes = ["single", "multi", "people", "tone", "platform"];
  if (choiceTypes.includes(question.type)) {
    const opts = el("div", "preflight-options");
    opts.setAttribute("role", question.type === "multi" ? "group" : "radiogroup");
    opts.setAttribute("aria-label", question.title);
    question.options.forEach((option) => {
      const btn = el("button", null, option);
      btn.type = "button";
      const selected = question.type === "multi" ? Array.isArray(question.answer) && question.answer.includes(option) : question.answer === option;
      btn.setAttribute("role", question.type === "multi" ? "checkbox" : "radio");
      btn.setAttribute("aria-checked", String(selected));
      btn.setAttribute("aria-pressed", String(selected));
      btn.addEventListener("click", () => {
        question.skipped = false;
        if (question.type === "multi") {
          const values = Array.isArray(question.answer) ? [...question.answer] : [];
          const index = values.indexOf(option);
          if (index >= 0) values.splice(index, 1); else values.push(option);
          question.answer = values;
        } else question.answer = option;
        renderPreflight();
      });
      opts.appendChild(btn);
    });
    wrap.appendChild(opts);
  } else {
    const input = el("input");
    input.type = ({ date: "date", time: "time" })[question.type] || "text";
    input.value = question.answer || "";
    input.setAttribute("aria-label", question.title);
    input.placeholder = question.type === "location" ? "輸入地點，或留空標示待填" : "輸入答案";
    input.addEventListener("input", () => { question.answer = input.value; question.skipped = false; $("preflight-confirm").disabled = !hasQuestionAnswer(question); });
    wrap.appendChild(input);
  }
  const otherLabel = el("label", "other-answer", "其他（自由輸入）");
  const other = el("input");
  other.type = "text";
  other.value = question.other || "";
  other.placeholder = "輸入其他答案";
  other.addEventListener("input", () => { question.other = other.value; question.skipped = false; $("preflight-confirm").disabled = !hasQuestionAnswer(question); });
  otherLabel.appendChild(other);
  wrap.appendChild(otherLabel);
  if (question.id === "schedule" && question.answer === "已確定") {
    const details = el("div", "schedule-fields");
    [["date", "date", "活動日期"], ["time", "time", "活動時間"], ["location", "text", "活動地點"]].forEach(([key, type, labelText]) => {
      const label = el("label", null, labelText);
      const input = el("input");
      input.type = type;
      input.value = (question.details && question.details[key]) || "";
      input.placeholder = key === "location" ? "輸入地點" : "";
      input.addEventListener("input", () => {
        question.details = question.details || {};
        question.details[key] = input.value;
      });
      label.appendChild(input);
      details.appendChild(label);
    });
    wrap.appendChild(details);
  }
  return wrap;
}

function renderPreflight() {
  const draft = state.preflight;
  if (!draft) return;
  const body = $("preflight-body");
  body.replaceChildren();
  const back = $("preflight-back");
  const skip = $("preflight-skip");
  const defaults = $("preflight-defaults");
  const cancel = $("preflight-cancel");
  const confirm = $("preflight-confirm");
  if (draft.stage === "questions") {
    const question = draft.questions[draft.current];
    const progress = el("p", "question-progress", "第 " + (draft.current + 1) + " 題／共 " + draft.questions.length + " 題");
    progress.setAttribute("aria-label", "目前第 " + (draft.current + 1) + " 題，共 " + draft.questions.length + " 題");
    body.appendChild(progress);
    const box = el("section", "preflight-question single-question");
    box.appendChild(el("h3", null, question.title));
    box.appendChild(renderQuestionControl(question));
    body.appendChild(box);
    body.appendChild(renderMiniSummary(draft));
    $("preflight-title").textContent = "在開始前，還需要確認關鍵資訊";
    confirm.textContent = draft.current === draft.questions.length - 1 ? "查看摘要" : "下一步";
    confirm.disabled = !hasQuestionAnswer(question);
    back.textContent = draft.current ? "返回上一題" : "返回修改";
    skip.textContent = "跳過此題";
    skip.hidden = false; defaults.hidden = true; cancel.hidden = true; back.hidden = false;
    setOrb("asking");
  } else {
    if (draft.decided) body.appendChild(el("p", "decision-note", "已套用合理預設；未確認的日期、地點與姓名會標示「待填」。"));
    body.appendChild(renderMiniSummary(draft, true));
    $("preflight-title").textContent = "確認任務摘要";
    confirm.textContent = "確認開始";
    confirm.disabled = false;
    back.textContent = "修改條件";
    skip.hidden = true; defaults.hidden = false; cancel.hidden = false; back.hidden = false;
    setOrb("asking", "請確認後開始");
  }
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
  setOrb("idle");
  $("input").focus();
}

function preflightPrompt(draft) {
  const answers = draft.questions.map((q) => {
    const details = q.details ? Object.entries(q.details).filter(([, value]) => value).map(([key, value]) => ({ date: "日期", time: "時間", location: "地點" })[key] + "：" + value).join("、") : "";
    return "- " + q.title + "：" + selectedAnswer(q) + (details ? "（" + details + "）" : "");
  }).join("\n");
  return draft.text + "\n\n【已確認的任務摘要】\n任務類型：" + taskKindName(draft.kind) + "\n" + (answers || "- 使用者確認需求已完整") + "\n請依此執行；未確認的日期、地點或姓名請清楚標示「待填」，不要自行杜撰。";
}

function advancePreflight() {
  const draft = state.preflight;
  if (!draft) return;
  if (draft.stage === "questions") {
    if (draft.current < draft.questions.length - 1) draft.current += 1;
    else draft.stage = "summary";
    renderPreflight();
    const first = $("preflight-body").querySelector("button, input");
    if (first) first.focus();
    return;
  }
  const attachments = state.attachments.slice();
  const displayText = draft.text;
  closePreflight();
  $("input").value = "";
  $("input").style.height = "auto";
  clearAttachments();
  runTask(preflightPrompt(draft), attachments, draft, displayText);
}

function skipPreflight() {
  const draft = state.preflight;
  if (!draft || draft.stage !== "questions") return;
  const question = draft.questions[draft.current];
  question.answer = "";
  question.other = "";
  question.skipped = true;
  advancePreflight();
}

function backPreflight() {
  const draft = state.preflight;
  if (!draft) return;
  if (draft.stage === "summary" && draft.questions.length) { draft.stage = "questions"; draft.current = draft.questions.length - 1; renderPreflight(); return; }
  if (draft.stage === "questions" && draft.current > 0) { draft.current -= 1; renderPreflight(); return; }
  closePreflight();
}

function usePreflightDefaults() {
  const draft = state.preflight;
  if (!draft) return;
  draft.questions.forEach((q) => { if (!q.answer && !q.other) { q.answer = q.default; q.skipped = false; } });
  draft.stage = "summary";
  renderPreflight();
}

function cancelPreflight() {
  closePreflight();
  state.preflight = null;
  announce("已取消任務，尚未開始執行");
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

function setBusyUI(busy) {
  // 執行中把送出鈕換成永遠摸得到的「停止生成」——
  // 不必展開工作進度也能停（進度卡裡的停止鈕仍在）。
  $("send").hidden = busy;
  $("send").disabled = busy;
  $("stop").hidden = !busy;
}

// 串流閒置逾時：超過這段時間沒收到任何資料就視為連線逾時
const STREAM_IDLE_TIMEOUT_MS = 120000;

function resetStreamWatchdog() {
  clearTimeout(state.streamWatchdog);
  state.streamWatchdog = setTimeout(() => {
    state.streamTimedOut = true;
    if (state.abortController) state.abortController.abort();
  }, STREAM_IDLE_TIMEOUT_MS);
}

function parseSSERecord(record) {
  // 支援標準 SSE 格式：event: / data:（可多行）/ 註解行（:）
  let eventName = "";
  const dataLines = [];
  for (const line of record.split(/\r?\n/)) {
    if (!line || line.startsWith(":")) continue;
    if (line.startsWith("event:")) {
      eventName = line.slice(6).trim();
      continue;
    }
    if (line.startsWith("data:")) {
      dataLines.push(line.slice(5).replace(/^\s/, ""));
    }
  }
  if (!dataLines.length) return null;
  try {
    const ev = JSON.parse(dataLines.join("\n"));
    if (eventName && ev && !ev.type) ev.type = eventName;
    return ev;
  } catch {
    console.warn("串流資料無法解析為 JSON，已略過這一段。");
    return null;
  }
}

async function requestStreamCancel() {
  // 通知伺服器停止生成並釋放這個 session 的執行鎖；
  // 就算這個請求失敗，本地 abort 仍會中斷連線。
  state.cancelRequested = true;
  try {
    if (state.sessionId) {
      await fetch("/api/chat/cancel", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: state.sessionId }),
      });
    }
  } catch {
    /* 後端取消失敗時仍會在本地中斷連線 */
  }
}

async function runTask(text, attachments = [], draft = null, displayText = "") {
  text = (text || $("input").value).trim();
  if (!text || state.busy) return;

  state.busy = true;
  state.lastPrompt = text;
  setBusyUI(true);
  showPanel("chat");
  addUser(displayText || (draft && draft.text) || text);
  $("input").value = "";
  $("input").style.height = "auto";
  announce("處理中");

  const turn = newTurn(text);
  state.activeTurn = turn;
  state.taskSummary = null;
  state.projectId = null;
  renderUnderstandingCard(turn, draft);
  seedProgress(turn);
  const workName = draft ? (preflightSummaryRows(draft).find(([key]) => key === "輸出格式") || ["", taskKindName(draft.kind)])[1] : "任務";
  getTimeline(turn).querySelector(".timeline-title").textContent = "正在製作" + workName;
  setFlowStep(turn, "understand", "running", "已理解需求", "正在整理目標與預期成果。");
  renderTaskControls(turn);
  state.abortController = new AbortController();
  setOrb("thinking");

  const retry = () => runTask(text, attachments, draft, displayText);

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
        attachments,
      }),
    });
    if (resp.status === 403) {
      const detail = await readDetail(resp);
      addError(turn, detail || "沒有權限執行這個操作", retry, "permission");
      announce("權限不足");
      return;
    }
    if (resp.status === 409) {
      const detail = await readDetail(resp);
      addError(turn, detail || "這個工作階段已有正在執行的任務，請先停止或稍候", retry, "busy");
      announce("任務執行中");
      return;
    }
    if (resp.status === 429) {
      const detail = await readDetail(resp);
      addError(turn, detail || "嘗試次數過多，請稍後再試", retry);
      announce(detail || "請稍後再試");
      return;
    }
    if (resp.status >= 500) {
      addError(turn, "伺服器發生錯誤，請稍後再試", retry, "busy");
      announce("伺服器錯誤");
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
    let sawDone = false;
    let sawCancelled = false;
    const dispatch = (record) => {
      const ev = parseSSERecord(record);
      if (!ev) return;
      if (ev.type === "done") sawDone = true;
      if (ev.type === "cancelled") sawCancelled = true;
      resetStreamWatchdog();
      handleEvent(turn, ev, retry);
    };
    resetStreamWatchdog();
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      resetStreamWatchdog();
      buffer += decoder.decode(value, { stream: true });
      const parts = buffer.split("\n\n");
      buffer = parts.pop() || "";
      for (const part of parts) {
        if (part.trim()) dispatch(part);
      }
    }
    // 最後一段可能沒有以空行收尾 —— 收線前一定要 flush
    buffer += decoder.decode();
    if (buffer.trim()) dispatch(buffer);

    if (!sawDone && !sawCancelled && !state.cancelRequested) {
      addError(turn, "連線中斷，任務可能沒有完成。請重試一次。", retry, "network");
      announce("連線中斷");
    }
  } catch (err) {
    if (err.name === "AbortError") {
      if (state.streamTimedOut) {
        addError(turn, "連線逾時，已停止等待。請確認網路後重試。", retry, "timeout");
        announce("連線逾時");
      } else {
        announce("已停止等待回覆");
        if (state.activeTurn) setFlowStep(turn, "verify", "failed", "已停止生成", "已保留完成的內容。");
        setOrb("idle", "已停止");
      }
      return;
    }
    if (err.message !== "needs-auth") {
      addError(turn, "連線中斷，請檢查網路後重試。", retry, "network");
      announce("連線中斷");
    }
  } finally {
    clearTimeout(state.streamWatchdog);
    state.streamWatchdog = null;
    state.cancelRequested = false;
    state.streamTimedOut = false;
    turn.querySelectorAll(".step.running").forEach((n) => n.classList.remove("running"));
    state.busy = false;
    setBusyUI(false);
    state.abortController = null;
    state.activeTurn = null;
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
      progressLine(turn, key, "◇", humanLabel(ev.label || ev.text, "進行中…"), humanLabel(ev.detail, ""));
      if (ev.status === "ok" || ev.ok === true || ev.done) finishStep(turn, key, true);
      if (ev.status === "fail" || ev.ok === false) finishStep(turn, key, false);
      announce(humanLabel(ev.label, "進行中"));
      break;
    }

    case "task_understood": {
      const produces = (ev.produces || []).join("、");
      setFlowStep(turn, "understand", "complete", "已理解需求", produces ? "預計產出：" + produces : "目標與成果已整理完成。");
      setFlowStep(turn, "facts", "running", "確認必要資料", "正在檢查已知資料與待填欄位。");
      refreshProject();
      break;
    }

    case "visual_analysis_started":
      setFlowStep(turn, "facts", "running", "理解圖片內容", "正在整理 " + (ev.count || 0) + " 張圖片中的可見資訊。");
      break;

    case "visual_analysis_completed":
      setFlowStep(turn, "facts", "complete", "理解圖片內容", "已整理圖片可見資訊，只用於這次任務。" );
      break;

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
      setFlowStep(turn, "draft", "running", "自動修正", "正在修正可自動處理的內容。");
      break;

    case "artifact":
    case "artifact_ready":
      addArtifact(turn, ev);
      break;

    case "message":
      if (ev.text) renderMessage(turn, ev.text);
      break;

    case "clarification_needed":
      progressLine(turn, "clarify", "?", "需要確認研究對象");
      finishStep(turn, "clarify", false);
      renderClarification(turn, ev);
      announce("需要確認研究對象");
      break;

    case "task_completed": {
      state.taskSummary = ev.summary || state.taskSummary;
      updateTaskDock(state.taskSummary);
      const rsLabel = ev.research_status_label;
      const okDone = !ev.research_status || ["complete", "internal", "partially_verified"].includes(ev.research_status);
      setFlowStep(
        turn, "verify", okDone ? "complete" : "failed", "最後檢查",
        rsLabel ? "研究狀態：" + rsLabel : "內容與待填欄位已完成檢查。"
      );
      const titleNode = getTimeline(turn).querySelector(".timeline-title");
      if (titleNode) titleNode.textContent = rsLabel || "產出完成";
      if (ev.research_status) renderResearchStatus(turn, { status: ev.research_status, label: rsLabel });
      renderCompletionActions(turn);
      announce(rsLabel || "完成");
      setOrb(okDone ? "complete" : "error");
      break;
    }

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
      setFlowStep(turn, "verify", "failed", "任務有一步需要處理", "可只重做失敗步驟，已完成內容會保留。");
      setOrb("error");
      break;

    case "source_cards":
      renderSourceCards(turn, ev);
      break;

    case "answer_review": {
      const n = (ev.claims || []).length;
      const ok = (ev.claims || []).filter((c) => c.status === "verified").length;
      progressLine(
        turn,
        "review",
        "✓",
        "來源驗證：" + (n ? ok + "/" + n + " 項結論已驗證" : "無外校結論需驗證"),
        (ev.notices || []).join("；")
      );
      finishStep(turn, "review", ev.verdict !== "block");
      break;
    }

    case "contamination_warning":
      progressLine(turn, "review", "✕", "資料歸屬錯誤，已暫停輸出");
      finishStep(turn, "review", false);
      renderContamination(turn, ev);
      announce("資料歸屬錯誤");
      break;

    case "research_status":
      renderResearchStatus(turn, ev);
      break;

    case "done":
      turn.querySelectorAll(".step.running").forEach((n) => n.classList.remove("running"));
      announce("完成");
      break;

    case "cancelled":
      turn.querySelectorAll(".step.running").forEach((n) => n.classList.remove("running"));
      setFlowStep(turn, "verify", "failed", "已停止生成", "已完成的內容會保留。");
      setOrb("idle", "已停止");
      announce("已停止生成");
      break;

    case "error":
      addError(turn, friendlyError(ev.text), retry, ev.error_code);
      announce(friendlyError(ev.text));
      break;

    default:
      // 未知事件不能弄壞整個任務：記中文警告後繼續
      console.warn("收到未知的串流事件類型，已略過：", ev.type);
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

function renderResumeExpired(message) {
  showPanel("chat");
  $("chat").replaceChildren();
  const box = el("div", "error");
  box.appendChild(el("strong", null, "工作階段已過期"));
  box.appendChild(el("div", null, message || "這個工作階段已過期或不存在，請建立新任務。"));
  const actions = el("div", "error-actions");
  const btn = el("button", "ghost", "建立新任務");
  btn.type = "button";
  btn.addEventListener("click", newChat);
  actions.appendChild(btn);
  box.appendChild(actions);
  $("chat").appendChild(box);
}

function renderResume(data) {
  state.sessionId = data.session_id;
  if (data.project_id) state.projectId = data.project_id;
  showPanel("chat");
  const chat = $("chat");
  chat.replaceChildren();

  // 任務脈絡標頭：標題 + 上次判斷的任務類型
  const head = el("div", "resume-head");
  head.appendChild(el("h2", null, "繼續「" + (data.title || "先前的任務") + "」"));
  const bits = [];
  if (data.task_label) bits.push("任務類型：" + data.task_label);
  if (data.updated_at) bits.push("上次更新：" + (formatWhen(data.updated_at) || data.updated_at));
  if (bits.length) head.appendChild(el("p", "resume-meta", bits.join("　·　")));
  chat.appendChild(head);

  // 對話太長：先給摘要與「查看完整紀錄」
  if (data.truncated) {
    const note = el("div", "resume-summary");
    note.appendChild(el("b", null, "先前進度摘要"));
    if (data.summary) {
      const body = el("div", "body");
      renderPlain(body, data.summary);
      note.appendChild(body);
    } else {
      note.appendChild(el("p", null, "以下只顯示最近 " + data.messages.length + " 則對話（共 " + data.message_count + " 則）。"));
    }
    const more = el("button", "ghost", "查看完整紀錄");
    more.type = "button";
    more.addEventListener("click", async () => {
      more.disabled = true;
      try {
        const resp = await api("/api/session/resume?full=1&session_id=" + encodeURIComponent(data.session_id));
        if (resp.ok) renderResume(await resp.json());
        else announce(BUSY_TEXT);
      } catch (err) {
        if (err.message !== "needs-auth") announce(BUSY_TEXT);
      } finally {
        more.disabled = false;
      }
    });
    note.appendChild(more);
    chat.appendChild(note);
  }

  // 還原最近幾輪對話（含網宣／研究卡片的渲染）
  for (const m of data.messages || []) {
    const t = newTurn();
    if (m.role === "user") t.appendChild(el("div", "bubble-user", m.text));
    else renderMessage(t, m.text);
  }

  // 還原目前產出
  if ((data.artifacts || []).length) {
    const t = newTurn();
    t.appendChild(el("p", "resume-section", "這個任務目前的產出"));
    for (const a of data.artifacts) addArtifact(t, a);
  }

  // 還原未完成步驟
  if ((data.pending_steps || []).length) {
    const t = newTurn();
    const tl = getTimeline(t);
    tl.open = true;
    const titleNode = tl.querySelector(".timeline-title");
    if (titleNode) titleNode.textContent = "尚未完成的步驟";
    const liveNode = tl.querySelector(".timeline-live");
    if (liveNode) liveNode.textContent = "待續接";
    data.pending_steps.forEach((step, i) => {
      pendingStep(t, "pending-" + i, step);
    });
  }

  chat.appendChild(el("p", "empty", "直接輸入下一步即可接續這個任務。"));
  $("input").focus();
  scrollChat();
}

async function continueSession(sid, title) {
  closeSessionSheet();
  try {
    const ensure = await api("/api/session", { method: "POST", body: JSON.stringify({ session_id: sid }) });
    if (!ensure.ok) {
      announce(BUSY_TEXT);
      return;
    }
    const ensured = await ensure.json();
    if (ensured.session_id !== sid) {
      // 後端找不到原 session、開了新的 —— 原任務已過期
      state.sessionId = ensured.session_id;
      renderResumeExpired("「" + (title || "先前的任務") + "」已過期或紀錄已被清除，已為你建立新的工作階段。");
      return;
    }
    const resp = await api("/api/session/resume?session_id=" + encodeURIComponent(sid));
    if (resp.status === 404) {
      renderResumeExpired();
      return;
    }
    if (!resp.ok) {
      announce(BUSY_TEXT);
      return;
    }
    renderResume(await resp.json());
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
    $("input").value = "";
    $("input").style.height = "auto";
    clearAttachments();
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
$("stop").addEventListener("click", () => controlTask("cancel", state.activeTurn || $("chat").lastElementChild));
function renderAttachmentStatus() {
  const status = $("attachment-status");
  const names = state.attachments.map((item) => item.name);
  status.hidden = !names.length;
  status.textContent = names.length ? "已加入圖片：" + names.join("、") + "（將交由 fal.ai 視覺服務僅供這次任務理解，不會保存）" : "";
}

function clearAttachments() {
  state.attachments = [];
  renderAttachmentStatus();
}

function readDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}

$("clear-input").addEventListener("click", () => {
  $("input").value = "";
  $("input").style.height = "auto";
  clearAttachments();
  $("input").focus();
  announce("已清除輸入內容");
});
$("attachment").addEventListener("change", async () => {
  const file = $("attachment").files && $("attachment").files[0];
  $("attachment").value = "";
  if (!file) return;
  if (file.type.startsWith("image/")) {
    if (!["image/jpeg", "image/png", "image/webp"].includes(file.type)) {
      announce("請選擇 JPG、PNG 或 WebP 圖片。");
      return;
    }
    if (file.size > 2_000_000) { announce("每張圖片需小於 2MB，請壓縮後再加入。"); return; }
    if (state.attachments.length >= 2) { announce("一次最多加入 2 張圖片。"); return; }
    try {
      const dataUrl = await readDataUrl(file);
      state.attachments.push({ name: file.name, media_type: file.type, data_url: dataUrl });
      $("input").value = ($("input").value + "\n【已加入圖片：「" + file.name + "」；請根據圖片中可見的內容完成需求。】").trim();
      $("input").dispatchEvent(new Event("input"));
      renderAttachmentStatus();
      announce("已加入圖片；只會傳給這次任務，不會保存。");
    } catch { announce("無法讀取這張圖片，請重新選取。"); }
    return;
  }
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
$("preflight-back").addEventListener("click", backPreflight);
$("preflight-skip").addEventListener("click", skipPreflight);
$("preflight-confirm").addEventListener("click", advancePreflight);
$("preflight-defaults").addEventListener("click", usePreflightDefaults);
$("preflight-cancel").addEventListener("click", cancelPreflight);
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
