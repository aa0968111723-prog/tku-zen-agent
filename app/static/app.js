/* 淡江大學領袖禪學社 · AI 代理 —— 前端 */

const $ = (id) => document.getElementById(id);
const chat = $("chat");
const input = $("input");
const sendBtn = $("send");
const destSel = $("destination");
const modelSel = $("model");
const banner = $("banner");
const hint = $("hint");

const SESSION_ID = "s_" + Math.random().toString(36).slice(2, 10);
let busy = false;

const FILE_ICONS = { xlsx: "▦", docx: "▤", pptx: "▣", gs: "⚡", md: "≡", pdf: "▪" };

/* ── 工具 ─────────────────────────────────────────── */

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
}

function scrollDown() {
  requestAnimationFrame(() => chat.scrollTo({ top: chat.scrollHeight, behavior: "smooth" }));
}

function clearWelcome() {
  const w = $("welcome");
  if (w) w.remove();
}

/** 極簡 Markdown → HTML，只處理 **粗體**、`程式碼`、- 項目、換行。 */
function renderText(node, text) {
  node.innerHTML = "";
  const lines = text.split("\n");
  let list = null;

  const inline = (s) => {
    const frag = document.createDocumentFragment();
    const parts = s.split(/(\*\*[^*]+\*\*|`[^`]+`)/g);
    for (const p of parts) {
      if (!p) continue;
      if (p.startsWith("**") && p.endsWith("**")) {
        frag.appendChild(el("strong", null, p.slice(2, -2)));
      } else if (p.startsWith("`") && p.endsWith("`") && p.length > 2) {
        frag.appendChild(el("code", null, p.slice(1, -1)));
      } else {
        frag.appendChild(document.createTextNode(p));
      }
    }
    return frag;
  };

  for (const line of lines) {
    const m = line.match(/^\s*[-*•]\s+(.*)$/);
    if (m) {
      if (!list) { list = el("ul"); list.style.margin = "6px 0"; list.style.paddingLeft = "22px"; node.appendChild(list); }
      const li = el("li");
      li.appendChild(inline(m[1]));
      list.appendChild(li);
      continue;
    }
    list = null;
    const p = el("div");
    if (line.trim() === "") { p.innerHTML = "&nbsp;"; p.style.height = "6px"; }
    else p.appendChild(inline(line));
    node.appendChild(p);
  }
}

/* ── 訊息區塊 ─────────────────────────────────────── */

function newTurn() {
  const t = el("div", "turn");
  chat.appendChild(t);
  return t;
}

function addUser(text) {
  const t = newTurn();
  t.appendChild(el("div", "bubble-user", text));
  scrollDown();
}

function addStatus(turn, text) {
  let s = turn.querySelector(".status");
  if (!s) { s = el("div", "status"); turn.appendChild(s); }
  s.textContent = text;
  scrollDown();
  return s;
}

function dropStatus(turn) {
  turn.querySelectorAll(".status").forEach((n) => n.remove());
}

function addAI(turn, text) {
  dropStatus(turn);
  const n = el("div", "bubble-ai");
  renderText(n, text);
  turn.appendChild(n);
  scrollDown();
}

function addError(turn, text) {
  dropStatus(turn);
  turn.appendChild(el("div", "error", "⚠  " + text));
  scrollDown();
}

function addToolStart(turn, ev) {
  dropStatus(turn);
  const card = el("div", "tool running");
  card.appendChild(el("span", "dot"));
  const body = el("div", "body");
  const label = el("b", null, ev.label + (ev.preview ? "：" + ev.preview : ""));
  body.appendChild(label);
  card.appendChild(body);
  turn.appendChild(card);
  scrollDown();
  return card;
}

function finishTool(card, ev) {
  if (!card) return;
  card.className = "tool " + (ev.ok ? "ok" : "fail");
  const body = card.querySelector(".body");
  if (ev.detail) {
    const d = el("span", "detail", ev.detail);
    body.appendChild(d);
  }
}

function addArtifact(turn, art) {
  const ext = (art.filename.split(".").pop() || "").toLowerCase();
  const card = el("div", "artifact");

  const icon = el("div", "icon", FILE_ICONS[ext] || "▪");
  card.appendChild(icon);

  const meta = el("div", "meta");
  meta.appendChild(el("div", "name", art.filename));
  const where = [];
  if (art.local_path) where.push("已存本機");
  if (art.drive_url) where.push("已上傳雲端");
  meta.appendChild(el("div", "where", where.join(" · ") || "已完成"));
  card.appendChild(meta);

  if (art.local_path) {
    const a = el("a", null, "下載");
    a.href = "/api/download?path=" + encodeURIComponent(art.local_path);
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

/* ── 送出 ─────────────────────────────────────────── */

async function send(text) {
  text = (text || input.value).trim();
  if (!text || busy) return;

  busy = true;
  sendBtn.disabled = true;
  clearWelcome();
  addUser(text);
  input.value = "";
  input.style.height = "auto";

  const turn = newTurn();
  addStatus(turn, "思考中");

  const toolCards = {};

  try {
    const resp = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        session_id: SESSION_ID,
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
        try { ev = JSON.parse(line.slice(5).trim()); } catch { continue; }

        switch (ev.type) {
          case "status":
            addStatus(turn, ev.text);
            break;
          case "message":
            addAI(turn, ev.text);
            break;
          case "tool_start":
            toolCards[ev.name] = addToolStart(turn, ev);
            break;
          case "tool_end":
            finishTool(toolCards[ev.name], ev);
            delete toolCards[ev.name];
            if (ev.artifact) addArtifact(turn, ev.artifact);
            break;
          case "error":
            addError(turn, ev.text);
            break;
          case "done":
            dropStatus(turn);
            break;
        }
      }
    }
  } catch (err) {
    addError(turn, "連線中斷：" + err.message);
  } finally {
    dropStatus(turn);
    busy = false;
    sendBtn.disabled = false;
    input.focus();
  }
}

/* ── 事件綁定 ─────────────────────────────────────── */

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
  await fetch("/api/reset", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: SESSION_ID }),
  });
  location.reload();
});

/* ── 啟動 ─────────────────────────────────────────── */

(async function init() {
  try {
    const h = await (await fetch("/api/health")).json();

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

    const kb = h.knowledge || {};
    hint.textContent =
      `知識庫 ${kb["檔案數"] || 0} 份檔案 / ${kb["段落數"] || 0} 段` +
      (kb["已載入雲端文件"] ? "（含歷年檔案）" : "") +
      ` · 產出存在 ${h.output_dir}` +
      (h.drive_ready ? "" : " · 雲端上傳未設定（見 README）");

    const problems = h.problems || [];
    if (problems.length) {
      banner.hidden = false;
      banner.innerHTML =
        "⚠ " +
        problems
          .map((p) => p.replace(/(https?:\/\/\S+)/g, '<a href="$1" target="_blank" rel="noopener">$1</a>'))
          .join("<br>");
    }
  } catch {
    banner.hidden = false;
    banner.textContent = "⚠ 連不上伺服器的 /api/health，請確認伺服器有正常啟動。";
  }
  input.focus();
})();
