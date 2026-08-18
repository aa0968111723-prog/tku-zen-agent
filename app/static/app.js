/* 淡江大學領袖禪學社 · AI 代理 —— 前端 */

const $ = (id) => document.getElementById(id);
const chat = $("chat");
const input = $("input");
const sendBtn = $("send");
const destSel = $("destination");
const modelSel = $("model");
const banner = $("banner");
const hint = $("hint");

let sessionId = null;
let busy = false;

const FILE_ICONS = { xlsx: "▦", docx: "▤", pptx: "▣", gs: "⚡", md: "≡", pdf: "▪" };

/* ── 小工具 ────────────────────────────────────────── */

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
}

const scrollDown = () =>
  requestAnimationFrame(() => chat.scrollTo({ top: chat.scrollHeight, behavior: "smooth" }));

async function api(path, options = {}) {
  const resp = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (resp.status === 401) {
    showGate();
    throw new Error("needs-auth");
  }
  return resp;
}

/* ── 存取碼畫面 ────────────────────────────────────── */

function showGate(message) {
  $("gate").hidden = false;
  $("app").hidden = true;
  if (message) {
    $("gate-error").textContent = message;
    $("gate-error").hidden = false;
  }
}

$("gate-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const resp = await fetch("/api/auth", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token: $("gate-token").value }),
  });
  if (resp.ok) {
    $("gate").hidden = true;
    $("app").hidden = false;
    init();
  } else {
    $("gate-error").textContent = "存取碼不正確。";
    $("gate-error").hidden = false;
  }
});

/* ── Markdown（極簡）─────────────────────────────── */

function renderText(node, text) {
  node.innerHTML = "";
  let list = null;

  const inline = (s) => {
    const frag = document.createDocumentFragment();
    for (const p of s.split(/(\*\*[^*]+\*\*|`[^`]+`)/g)) {
      if (!p) continue;
      if (p.startsWith("**") && p.endsWith("**")) frag.appendChild(el("strong", null, p.slice(2, -2)));
      else if (p.startsWith("`") && p.endsWith("`") && p.length > 2)
        frag.appendChild(el("code", null, p.slice(1, -1)));
      else frag.appendChild(document.createTextNode(p));
    }
    return frag;
  };

  for (const line of text.split("\n")) {
    const m = line.match(/^\s*[-*•]\s+(.*)$/);
    if (m) {
      if (!list) {
        list = el("ul");
        list.style.margin = "6px 0";
        list.style.paddingLeft = "22px";
        node.appendChild(list);
      }
      const li = el("li");
      li.appendChild(inline(m[1]));
      list.appendChild(li);
      continue;
    }
    list = null;
    const p = el("div");
    if (!line.trim()) {
      p.innerHTML = "&nbsp;";
      p.style.height = "6px";
    } else p.appendChild(inline(line));
    node.appendChild(p);
  }
}

/* ── 訊息區塊 ──────────────────────────────────────── */

function newTurn() {
  const t = el("div", "turn");
  chat.appendChild(t);
  return t;
}

function addUser(text) {
  newTurn().appendChild(el("div", "bubble-user", text));
  scrollDown();
}

function addAI(turn, text) {
  const n = el("div", "bubble-ai");
  renderText(n, text);
  turn.appendChild(n);
  scrollDown();
}

function addError(turn, text) {
  turn.appendChild(el("div", "error", "⚠  " + text));
  scrollDown();
}

/* 進度條：一行一步，不顯示推理過程 */
function progressLine(turn, key, icon, label, detail) {
  let row = turn.querySelector(`[data-step="${key}"]`);
  if (!row) {
    row = el("div", "step running");
    row.dataset.step = key;
    row.appendChild(el("span", "step-icon", icon));
    const body = el("div", "step-body");
    body.appendChild(el("b", null, label));
    row.appendChild(body);
    turn.appendChild(row);
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
  scrollDown();
  return row;
}

function finishStep(turn, key, ok) {
  const row = turn.querySelector(`[data-step="${key}"]`);
  if (row) row.className = "step " + (ok ? "ok" : "fail");
}

function addArtifact(turn, art) {
  const ext = (art.filename || "").split(".").pop().toLowerCase();
  const card = el("div", "artifact");
  card.appendChild(el("div", "icon", FILE_ICONS[ext] || "▪"));

  const meta = el("div", "meta");
  meta.appendChild(el("div", "name", art.filename));
  const bits = [];
  if (art.version > 1) bits.push(`第 ${art.version} 版`);
  bits.push(art.verified ? "已通過檢查" : "檢查有警告");
  if (art.drive_url) bits.push("已上傳雲端");
  meta.appendChild(el("div", "where", bits.join(" · ")));
  if (art.warning) meta.appendChild(el("div", "warn", art.warning));
  card.appendChild(meta);

  if (art.artifact_id) {
    const a = el("a", null, "下載");
    a.href = "/api/download?artifact_id=" + encodeURIComponent(art.artifact_id);
    card.appendChild(a);
  }
  if (art.drive_url) {
    const a = el("a", null, "開雲端");
    a.href = art.drive_url;
    a.target = "_blank";
    a.rel = "noopener";
    card.appendChild(a);
  }

  turn.appendChild(card);
  scrollDown();
}

/* ── 送出 ──────────────────────────────────────────── */

async function send(text) {
  text = (text || input.value).trim();
  if (!text || busy) return;

  busy = true;
  sendBtn.disabled = true;
  $("welcome")?.remove();
  addUser(text);
  input.value = "";
  input.style.height = "auto";

  const turn = newTurn();
  progressLine(turn, "understand", "◇", "理解需求…");

  try {
    const resp = await api("/api/chat", {
      method: "POST",
      body: JSON.stringify({
        session_id: sessionId,
        message: text,
        destination: destSel.value,
        model: modelSel.value || null,
      }),
    });
    if (!resp.ok || !resp.body) throw new Error("伺服器沒有回應（HTTP " + resp.status + "）");

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
        handleEvent(turn, ev);
      }
    }
  } catch (err) {
    if (err.message !== "needs-auth") addError(turn, "連線中斷：" + err.message);
  } finally {
    turn.querySelectorAll(".step.running").forEach((n) => n.classList.remove("running"));
    busy = false;
    sendBtn.disabled = false;
    input.focus();
  }
}

function handleEvent(turn, ev) {
  switch (ev.type) {
    case "session":
      sessionId = ev.session_id;
      break;

    case "task_understood": {
      const produces = (ev.produces || []).join("、");
      finishStep(turn, "understand", true);
      progressLine(turn, "understand", "◇", `已理解：${ev.skill}` + (produces ? `（要做${produces}）` : ""));
      finishStep(turn, "understand", true);
      break;
    }

    case "plan_created": {
      progressLine(turn, "plan", "☰", "計畫：" + (ev.steps || []).join(" → "));
      finishStep(turn, "plan", true);
      if (ev.missing_facts?.length) {
        progressLine(turn, "missing", "!", `本學期尚未設定：${ev.missing_facts.join("、")}`, "這些欄位會填「待填」");
        finishStep(turn, "missing", false);
      }
      break;
    }

    case "retrieval_started":
      progressLine(turn, "retrieval", "⌕", "查知識庫…");
      break;

    case "retrieval_result":
      progressLine(
        turn,
        "retrieval",
        "⌕",
        `找到 ${ev.count} 段（規範 ${ev.curated}、歷年範例 ${ev.archive}）`,
        (ev.sources || []).slice(0, 4).join("　·　")
      );
      finishStep(turn, "retrieval", true);
      break;

    case "tool_started":
      progressLine(turn, "tool-" + ev.name, "▸", ev.label + (ev.preview ? "：" + ev.preview : ""));
      break;

    case "tool_completed":
      progressLine(turn, "tool-" + ev.name, "▸", ev.label, ev.detail);
      finishStep(turn, "tool-" + ev.name, ev.ok);
      break;

    case "verification_started":
      progressLine(turn, "verify", "✓", `檢查 ${ev.filename}…`);
      break;

    case "verification_result":
      progressLine(turn, "verify", "✓", ev.summary, (ev.errors || []).concat(ev.warnings || []).join("；"));
      finishStep(turn, "verify", ev.ok);
      break;

    case "repair_started":
      progressLine(turn, "repair", "↻", `自動修正（第 ${ev.attempt} 次）`, (ev.reasons || []).join("；"));
      break;

    case "artifact_ready":
      finishStep(turn, "repair", true);
      addArtifact(turn, ev);
      break;

    case "message":
      addAI(turn, ev.text);
      break;

    case "task_completed":
      progressLine(turn, "done", "●", "完成");
      finishStep(turn, "done", true);
      break;

    case "error":
      addError(turn, ev.text);
      break;
  }
}

/* ── 本學期設定 ────────────────────────────────────── */

async function openTerm() {
  const data = await (await api("/api/term")).json();
  const form = $("term-form");
  form.innerHTML = "";

  for (const f of data.fields) {
    const wrap = el("label", "term-field");
    const head = el("span", "term-label");
    head.appendChild(document.createTextNode(f.label));
    if (!f.known) head.appendChild(el("em", "unset", "未設定"));
    wrap.appendChild(head);

    const control = f.kind === "longtext" || f.kind === "list" ? el("textarea") : el("input");
    control.name = f.key;
    control.value = f.value;
    control.placeholder = f.hint;
    if (control.tagName === "TEXTAREA") control.rows = f.kind === "list" ? 4 : 2;
    wrap.appendChild(control);
    form.appendChild(wrap);
  }

  $("term-status").textContent = data.updated_at ? `最後更新：${data.updated_at}` : "尚未設定過";
  $("term-modal").hidden = false;
}

async function saveTerm() {
  const fields = {};
  for (const c of $("term-form").querySelectorAll("input, textarea")) fields[c.name] = c.value;
  const data = await (await api("/api/term", { method: "POST", body: JSON.stringify({ fields }) })).json();
  $("term-modal").hidden = true;
  $("term-label").textContent = data.term_label + " · AI 代理";
  refreshHint();
}

$("term-btn").addEventListener("click", openTerm);
$("term-save").addEventListener("click", saveTerm);
$("term-close").addEventListener("click", () => ($("term-modal").hidden = true));
$("term-cancel").addEventListener("click", () => ($("term-modal").hidden = true));
$("term-modal").addEventListener("click", (e) => {
  if (e.target.id === "term-modal") $("term-modal").hidden = true;
});

/* ── 其他事件 ──────────────────────────────────────── */

sendBtn.addEventListener("click", () => send());

input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
    e.preventDefault();
    send();
  }
});

input.addEventListener("input", () => {
  input.style.height = "auto";
  input.style.height = Math.min(input.scrollHeight, 200) + "px";
});

$("chips").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-prompt]");
  if (btn) send(btn.dataset.prompt);
});

$("reset").addEventListener("click", async () => {
  if (sessionId) await api("/api/reset", { method: "POST", body: JSON.stringify({ session_id: sessionId }) });
  location.reload();
});

/* ── 啟動 ──────────────────────────────────────────── */

let healthCache = null;

function refreshHint() {
  if (!healthCache) return;
  const kb = healthCache.knowledge || {};
  const term = healthCache.term || {};
  const bits = [`知識庫 ${kb["檔案數"] || 0} 份 / ${kb["段落數"] || 0} 段`];
  if (kb["語意層"]) bits.push(kb["語意層"]);
  if (term.missing?.length) bits.push(`本學期尚未設定：${term.missing.slice(0, 3).join("、")}`);
  if (!healthCache.drive_ready) bits.push("雲端上傳未設定");
  hint.textContent = bits.join(" · ");
}

async function init() {
  try {
    const h = await (await api("/api/health")).json();
    healthCache = h;

    for (const m of h.models || []) {
      const o = el("option", null, m.split("/").pop());
      o.value = m;
      if (m === h.model) o.selected = true;
      modelSel.appendChild(o);
    }
    if (!(h.models || []).includes(h.model)) {
      const o = el("option", null, h.model);
      o.value = h.model;
      o.selected = true;
      modelSel.insertBefore(o, modelSel.firstChild);
    }

    destSel.value = h.destination || "local";
    if (h.term?.label) $("term-label").textContent = h.term.label + " · AI 代理";

    const problems = [...(h.problems || [])];
    if (!h.term?.configured) {
      problems.push("還沒設定本學期資料。代理不會猜今年的社長、社課時間、社費——請按右上角「本學期設定」填寫。");
    }
    if (problems.length) {
      banner.hidden = false;
      banner.innerHTML =
        "⚠ " +
        problems
          .map((p) => p.replace(/(https?:\/\/\S+)/g, '<a href="$1" target="_blank" rel="noopener">$1</a>'))
          .join("<br>");
    }

    refreshHint();

    const s = await (await api("/api/session", { method: "POST", body: JSON.stringify({}) })).json();
    sessionId = s.session_id;

    input.focus();
  } catch (err) {
    if (err.message === "needs-auth") return;
    banner.hidden = false;
    banner.textContent = "⚠ 連不上伺服器，請確認它有正常啟動。";
  }
}

(async function boot() {
  const status = await (await fetch("/api/auth")).json();
  if (status.mode === "token" && !status.authenticated) {
    showGate();
  } else {
    $("app").hidden = false;
    init();
  }
})();
