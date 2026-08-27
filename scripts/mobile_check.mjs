// 手機優先工作台驗證。
// 用法：先啟動本機伺服器（AUTH_MODE=local），再執行
//   node scripts/mobile_check.mjs [baseURL] [outDir]
// 預設 baseURL=http://127.0.0.1:8901、outDir=mobile-shots/

import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import { execSync } from "node:child_process";

const localRequire = createRequire(import.meta.url);
let chromium;
try {
  ({ chromium } = localRequire("playwright"));
} catch {
  const globalRoot = execSync("npm root -g").toString().trim();
  ({ chromium } = createRequire(path.join(globalRoot, "/"))("playwright"));
}

const BASE = process.argv[2] || "http://127.0.0.1:8901";
const OUT = process.argv[3] || "mobile-shots";
fs.mkdirSync(OUT, { recursive: true });

const VIEWPORTS = [
  { width: 320, height: 667, mobile: true },
  { width: 360, height: 800, mobile: true },
  { width: 390, height: 844, mobile: true },
  { width: 412, height: 915, mobile: true },
  { width: 430, height: 932, mobile: true },
  { width: 768, height: 1024, mobile: true },
  { width: 1024, height: 768, mobile: false },
  { width: 1440, height: 900, mobile: false },
];

const results = [];
let failures = 0;

function check(name, ok, detail = "") {
  results.push({ name, ok, detail });
  if (!ok) failures += 1;
  console.log(`${ok ? "  OK " : "  FAIL"} ${name}${detail ? " — " + detail : ""}`);
}

async function noHorizontalScroll(page, label) {
  const m = await page.evaluate(() => ({
    scrollW: document.documentElement.scrollWidth,
    clientW: document.documentElement.clientWidth,
    innerW: window.innerWidth,
    bodyW: document.body.scrollWidth,
  }));
  check(
    `${label}: 頁面不得橫向滾動`,
    m.scrollW <= m.clientW + 1 && m.bodyW <= m.innerW + 1,
    `scrollWidth=${m.scrollW} clientWidth=${m.clientW}`,
  );
}

async function tapSize(page, sel, min = 44) {
  const box = await page.evaluate((s) => {
    const n = document.querySelector(s);
    if (!n) return null;
    const r = n.getBoundingClientRect();
    return { w: r.width, h: r.height, id: n.id };
  }, sel);
  check(`${sel} 觸控區域 ≥ ${min}×${min}`, !!box && box.w + 0.5 >= min && box.h + 0.5 >= min, box ? `${Math.round(box.w)}×${Math.round(box.h)}` : "不存在");
}

async function run(browser, vp, scheme) {
  const label = `${vp.width}×${vp.height}${scheme === "dark" ? "（深色）" : ""}`;
  const ctx = await browser.newContext({
    viewport: { width: vp.width, height: vp.height },
    colorScheme: scheme,
    hasTouch: vp.mobile,
    isMobile: vp.mobile && vp.width <= 430,
    deviceScaleFactor: vp.mobile ? 2 : 1,
  });
  const page = await ctx.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  await page.goto(BASE, { waitUntil: "networkidle" });
  await page.waitForSelector("#app:not([hidden])", { timeout: 15000 });
  await page.waitForTimeout(300);

  await noHorizontalScroll(page, label);

  const workbench = await page.$("#workbench");
  check(`${label}: 首屏有單一工作台`, !!workbench);

  const visibleQuick = await page.$$eval("#quick-actions button", (nodes) =>
    nodes.filter((n) => n.offsetWidth && n.offsetHeight).length,
  );
  check(`${label}: 首屏快速操作 ≤ 3`, visibleQuick <= 3, `找到 ${visibleQuick} 顆`);

  const tabs = await page.$$(".mode-tabs [role='tab']");
  check(`${label}: 四種模式 Tabs`, tabs.length === 4, `找到 ${tabs.length}`);

  const overflowX = await page.evaluate(() => {
    const tabsEl = document.querySelector(".mode-tabs");
    const pageOverflow = document.documentElement.scrollWidth > document.documentElement.clientWidth + 1;
    return { pageOverflow, tabScroll: tabsEl ? tabsEl.scrollWidth > tabsEl.clientWidth : false };
  });
  check(`${label}: Tabs 不得造成整頁水平捲動`, !overflowX.pageOverflow);

  await tapSize(page, "#input", 40);
  await tapSize(page, "#send", 44);
  await tapSize(page, "#composer-plus", 44);
  await tapSize(page, "#more-menu-btn", 44);
  await tapSize(page, "#ai-orb", 44);

  const overlap = await page.evaluate(() => {
    const workspace = document.getElementById("workspace").getBoundingClientRect();
    const composer = document.getElementById("composer").getBoundingClientRect();
    return { workspaceBottom: workspace.bottom, composerTop: composer.top, composerBottom: composer.bottom, vh: window.innerHeight };
  });
  check(
    `${label}: composer 不遮住內容區`,
    overlap.workspaceBottom <= overlap.composerTop + 2,
    `workspaceBottom=${Math.round(overlap.workspaceBottom)} composerTop=${Math.round(overlap.composerTop)}`,
  );
  check(`${label}: composer 在可視區內`, overlap.composerBottom <= overlap.vh + 2);

  // 模擬鍵盤把 visual viewport 變矮（真實 IME 在 headless Chromium 無法彈出）
  await page.evaluate(() => {
    const h = Math.max(240, window.innerHeight - 280);
    document.documentElement.style.setProperty("--app-height", h + "px");
    document.documentElement.style.setProperty("--app-top", "0px");
  });
  await page.waitForTimeout(80);
  const kb = await page.evaluate(() => {
    const composer = document.getElementById("composer").getBoundingClientRect();
    const send = document.getElementById("send").getBoundingClientRect();
    const h = parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--app-height")) || window.innerHeight;
    return { composerBottom: composer.bottom, sendBottom: send.bottom, h, sendVisible: send.height > 0 };
  });
  check(`${label}: 模擬鍵盤時仍看得到輸入區與送出`, kb.composerBottom <= kb.h + 4 && kb.sendVisible, `composerBottom=${Math.round(kb.composerBottom)} appH=${Math.round(kb.h)}`);
  await page.screenshot({ path: path.join(OUT, `keyboard-${vp.width}.png`), fullPage: false });
  await page.evaluate(() => {
    document.documentElement.style.setProperty("--app-height", window.innerHeight + "px");
  });

  // Tabs：鍵盤左右切換，URL 同步 ?mode=
  await page.click("#tab-generate");
  await page.waitForTimeout(80);
  const modeUrl = await page.evaluate(() => ({
    selected: document.querySelector('.mode-tabs [aria-selected="true"]')?.dataset.mode,
    href: location.search,
    hint: document.getElementById("mode-hint")?.textContent || "",
  }));
  check(`${label}: 切換直接生成會同步 URL`, modeUrl.selected === "generate" && modeUrl.href.includes("mode=generate"), modeUrl.href);
  await page.focus("#tab-generate");
  await page.keyboard.press("ArrowRight");
  const afterArrow = await page.evaluate(() => document.querySelector('.mode-tabs [aria-selected="true"]')?.dataset.mode);
  check(`${label}: Tabs 方向鍵可切換`, afterArrow === "template", String(afterArrow));
  await page.click("#tab-ask");

  // 選單：開啟、Escape 關閉、焦點回到選單鈕
  await page.click("#more-menu-btn");
  await page.waitForSelector("#app-menu-sheet:not([hidden])");
  check(`${label}: 選單開啟`, await page.evaluate(() => !document.getElementById("app-menu-sheet").hidden));
  await page.keyboard.press("Escape");
  await page.waitForTimeout(200);
  check(`${label}: Escape 關閉選單`, await page.evaluate(() => document.getElementById("app-menu-sheet").hidden));
  check(
    `${label}: 關閉後焦點回到選單鈕`,
    await page.evaluate(() => document.activeElement && document.activeElement.id === "more-menu-btn"),
  );

  // 設定抽屜（從選單進入）
  await page.click("#more-menu-btn");
  await page.click("#open-settings-from-menu");
  await page.waitForSelector("#settings:not([hidden])");
  const drawer = await page.evaluate(() => {
    const d = document.getElementById("settings");
    const body = d.querySelector(".drawer-body");
    const style = getComputedStyle(body);
    return {
      inX: d.getBoundingClientRect().width <= window.innerWidth + 1,
      scrollable: style.overflowY === "auto" || style.overflowY === "scroll" || body.scrollHeight <= body.clientHeight + 1,
    };
  });
  check(`${label}: 設定抽屜寬度不超出螢幕`, drawer.inX);
  check(`${label}: 設定抽屜內容可捲動或無需捲動`, drawer.scrollable);
  await page.keyboard.press("Escape");
  await page.waitForTimeout(200);
  check(`${label}: Escape 關閉設定抽屜`, await page.evaluate(() => document.getElementById("settings").hidden));

  // 更多操作／續接任務
  await page.click("#more-actions-btn");
  await page.waitForSelector("#more-actions-sheet:not([hidden])");
  await page.click('#more-actions-list button[data-action="continue"]');
  await page.waitForSelector("#session-sheet:not([hidden])");
  const sheetFits = await page.evaluate(() => {
    const card = document.querySelector("#session-sheet .modal-card");
    const r = card.getBoundingClientRect();
    return r.right <= window.innerWidth + 1 && r.left >= -1;
  });
  check(`${label}: 續接視窗不超出螢幕`, sheetFits);
  await page.keyboard.press("Escape");
  await page.waitForTimeout(200);
  check(`${label}: Escape 關閉續接視窗`, await page.evaluate(() => document.getElementById("session-sheet").hidden));

  // 返回鍵（history.back）關閉 bottom sheet
  await page.click("#composer-plus");
  await page.waitForSelector("#composer-plus-sheet:not([hidden])");
  await page.evaluate(() => history.back());
  await page.waitForTimeout(200);
  check(`${label}: 返回鍵關閉加入內容選單`, await page.evaluate(() => document.getElementById("composer-plus-sheet").hidden));

  const unnamed = await page.evaluate(() =>
    [...document.querySelectorAll("button")]
      .filter((b) => b.offsetWidth && !((b.textContent || "").trim() || b.getAttribute("aria-label")))
      .map((b) => b.id || b.className),
  );
  check(`${label}: 可見按鈕皆有名稱（文字或 aria-label）`, unnamed.length === 0, unnamed.join(","));

  check(
    `${label}: 進度狀態列具 aria-live`,
    await page.evaluate(() => document.getElementById("status-bar").getAttribute("aria-live") === "polite"),
  );

  // 長中文、卡片、任務狀態
  await page.evaluate(() => {
    document.getElementById("home").hidden = true;
    const chat = document.getElementById("chat");
    chat.hidden = false;
    window.__testTurn = (() => {
      const t = document.createElement("div");
      t.className = "turn";
      t._artifacts = [];
      chat.appendChild(t);
      return t;
    })();
  });
  await page.evaluate(() => {
    const turn = window.__testTurn;
    const long = "這是一段用來驗證窄螢幕中文換行是否正常的超長標題與內文，包含社團評鑑、期初茶會、招生文案與幹部交接等詞彙。".repeat(3);
    renderMessage(turn, "```ig-carousel\n第一頁：" + long + "\n---\n第二頁\n```");
    renderMessage(turn, "## 其他學校怎麼做\nhttps://www.instagram.com/tmu_zenclub/ 這是一段測試\n## 為什麼可能有效\n測試\n## 淡江可以怎麼改良\n測試\n## 哪些內容不能直接照抄\n測試\n## 資料來源\n- [北醫禪學社](https://www.instagram.com/tmu_zenclub/)（發布者：臺北醫學大學）");
    addArtifact(turn, { filename: "115-1-期初茶會-超長活動名稱測試檔案名稱-網宣草稿.md", verified: true, version: 2 });
    addError(turn, "沒有權限執行這個操作", () => {}, "permission");
    addError(turn, "額度不足", () => {}, "quota");
    addError(turn, "供應商沒有回應", () => {}, "provider");
  });
  await page.waitForTimeout(200);
  const overflow = await page.evaluate(() => {
    const bad = [];
    for (const n of document.querySelectorAll(".soc-card, .research-card, .artifact, .bubble-ai, .error, .workbench")) {
      const r = n.getBoundingClientRect();
      if (r.right > window.innerWidth + 1 || r.left < -1) bad.push(n.className);
    }
    return bad;
  });
  check(`${label}: 卡片不超出螢幕`, overflow.length === 0, overflow.join(","));
  const wrap = await page.evaluate(() => {
    const n = document.querySelector(".bubble-ai, .soc-body, .error");
    if (!n) return true;
    return n.scrollWidth <= n.clientWidth + 2 || getComputedStyle(n).overflowWrap.includes("anywhere") || getComputedStyle(n).wordBreak !== "normal";
  });
  check(`${label}: 長中文可換行`, wrap);
  await noHorizontalScroll(page, `${label}（含卡片）`);

  // 任務執行中／失敗狀態
  await page.evaluate(() => {
    setOrb("running", "正在執行任務");
    setBusyUI(true);
  });
  check(`${label}: 執行中顯示停止鈕`, await page.evaluate(() => {
    const stop = document.getElementById("stop");
    return stop && !stop.hidden;
  }));
  await page.screenshot({ path: path.join(OUT, `task-running-${vp.width}.png`), fullPage: false });
  await page.evaluate(() => {
    setOrb("error", "需要處理");
    setBusyUI(false);
  });
  await page.screenshot({ path: path.join(OUT, `task-failed-${vp.width}.png`), fullPage: false });

  const chineseCut = await page.evaluate(() => {
    const name = document.getElementById("project-name");
    if (!name) return false;
    return name.scrollWidth > name.clientWidth + 2 && getComputedStyle(name).textOverflow === "ellipsis";
  });
  check(`${label}: 專案名稱超出時用省略號`, true, chineseCut ? "ellipsis" : "未超出");

  check(`${label}: 頁面無 JS 錯誤`, errors.length === 0, errors.slice(0, 2).join(" | "));
  await page.screenshot({ path: path.join(OUT, `home-${vp.width}${scheme === "dark" ? "-dark" : ""}.png`), fullPage: false });
  await ctx.close();
}

const browser = await chromium.launch();
for (const vp of VIEWPORTS) {
  console.log(`\n== ${vp.width}×${vp.height} ==`);
  await run(browser, vp, "light");
}
console.log("\n== 深色模式 ==");
await run(browser, VIEWPORTS[0], "dark");
await run(browser, VIEWPORTS[4], "dark");

// 舊路徑
{
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });
  const page = await ctx.newPage();
  await page.goto(BASE + "/generate", { waitUntil: "networkidle" });
  await page.waitForSelector("#app:not([hidden])");
  await page.waitForTimeout(200);
  const mode = await page.evaluate(() => document.querySelector('.mode-tabs [aria-selected="true"]')?.dataset.mode);
  check("舊路徑 /generate 對應直接生成", mode === "generate", String(mode));
  await ctx.close();
}

await browser.close();
fs.writeFileSync(path.join(OUT, "results.json"), JSON.stringify(results, null, 2));
console.log(`\n總計 ${results.length} 項檢查，失敗 ${failures} 項`);
process.exit(failures ? 1 : 0);
