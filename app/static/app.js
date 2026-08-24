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

function addError(turn, text, retryFn) {
  const box = el("div", "error");
  box.appendChild(document.createTextNode(text));
  if (retryFn) {
    const actions = el("div", "error-actions");
    const btn = el("button", "ghost", "重試");
    btn.type = "button";
    btn.addEventListener("click", retryFn);
    actions.appendChild(btn);
    box.appendChild(actions);
  }
  turn.appendChild(box);
  scrollChat();
}

function getTimeline(turn) {
  let tl = turn.querySelector(".timeline");
  if (!tl) {
    tl = el("details", "timeline");
    tl.open = true;
    tl.appendChild(el("summary", "timeline-sum", "工作進度"));
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
  if (row) row.className = "step " + (ok ? "ok" : "fail");
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
  card.appendChild(body);
  turn.appendChild(card);
  turn.querySelectorAll("[data-card]").forEach((c) => c._refreshDl && c._refreshDl());
  scrollChat();
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

async function ensureSession() {
  if (state.sessionId) return state.sessionId;
  const resp = await api("/api/session", { method: "POST", body: JSON.stringify({}) });
  if (!resp.ok) throw new Error("session");
  const data = await resp.json();
  state.sessionId = data.session_id;
  return state.sessionId;
}

async function send(text) {
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
  progressLine(turn, "understand", "◇", "理解需求…");

  const retry = () => send(text);

  try {
    const sid = await ensureSession();
    const resp = await api("/api/chat", {
      method: "POST",
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
    if (err.message !== "needs-auth") {
      addError(turn, BUSY_TEXT, retry);
      announce(BUSY_TEXT);
    }
  } finally {
    turn.querySelectorAll(".step.running").forEach((n) => n.classList.remove("running"));
    state.busy = false;
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
      finishStep(turn, "understand", true);
      progressLine(
        turn,
        "understand",
        "◇",
        "已理解：" + (ev.skill || "任務") + (produces ? "（要做" + produces + "）" : "")
      );
      finishStep(turn, "understand", true);
      break;
    }

    case "plan_created": {
      progressLine(turn, "plan", "☰", "計畫：" + (ev.steps || []).join(" → "));
      finishStep(turn, "plan", true);
      if (ev.missing_facts && ev.missing_facts.length) {
        progressLine(turn, "missing", "!", "本學期尚未設定：" + ev.missing_facts.join("、"), "這些欄位會填「待填」");
        finishStep(turn, "missing", false);
      }
      break;
    }

    case "retrieval_started":
      progressLine(turn, "retrieval", "⌕", "查資料…");
      break;

    case "retrieval_result":
      progressLine(turn, "retrieval", "⌕", "找到 " + (ev.count || 0) + " 段參考", (ev.sources || []).slice(0, 4).join("　·　"));
      finishStep(turn, "retrieval", true);
      break;

    case "step_started":
      progressLine(turn, ev.step_id || "workflow-step", "◇", ev.description || "執行下一步…");
      break;

    case "step_failed":
      progressLine(turn, ev.step_id || "workflow-step", "!", "這一步需要重試", ev.text || ev.error_code || "");
      finishStep(turn, ev.step_id || "workflow-step", false);
      break;

    case "research_sources_saved":
      progressLine(turn, "research-sources", "⌕", "已保存研究來源", "之後產出可以回到來源檢查");
      finishStep(turn, "research-sources", true);
      break;

    case "tool_started": {
      const key = pushToolKey(turn, ev.name, callId);
      progressLine(turn, key, "▸", (ev.label || ev.name || "工具") + (ev.preview ? "：" + ev.preview : ""));
      break;
    }

    case "tool_completed": {
      const key = popToolKey(turn, ev.name, callId);
      progressLine(turn, key, "▸", ev.label || ev.name || "工具", ev.detail);
      finishStep(turn, key, ev.ok !== false);
      break;
    }

    case "verification_started":
      progressLine(turn, "verify", "✓", "檢查 " + (ev.filename || "產出") + "…");
      break;

    case "verification_result":
      progressLine(
        turn,
        "verify",
        "✓",
        ev.summary || "檢查完成",
        (ev.errors || []).concat(ev.warnings || []).join("；")
      );
      finishStep(turn, "verify", ev.ok);
      break;

    case "repair_started":
      progressLine(turn, "repair", "↻", "自動修正（第 " + ev.attempt + " 次）", (ev.reasons || []).join("；"));
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
      progressLine(turn, "done", "●", "完成");
      finishStep(turn, "done", true);
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
      progressLine(turn, "workflow-status", "!", "任務有一步失敗", "可重試失敗步驟");
      finishStep(turn, "workflow-status", false);
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
  if (action === "ig") showPanel("ig");
  else if (action === "research") showPanel("research");
  else if (action === "continue") openSessionSheet();
  else if (PROMPTS[action]) {
    $("input").value = PROMPTS[action];
    $("input").focus();
    $("input").dispatchEvent(new Event("input"));
  }
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

document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  if (!$("term-modal").hidden) closeTerm();
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
