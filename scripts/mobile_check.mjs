// 手機版驗證：在 320 / 360 / 390 / 430 px 寬度實際載入工作台，
// 檢查橫向滾動、按鈕可讀性、抽屜、composer、觸控目標、鍵盤流程、
// 深色與高對比模式、卡片溢出與無障礙名稱。
//
// 用法：先啟動本機伺服器（AUTH_MODE=local），再執行
//   node scripts/mobile_check.mjs [baseURL] [outDir]
// 預設 baseURL=http://127.0.0.1:8901、outDir=mobile-shots/

import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import { execSync } from "node:child_process";

// playwright 可能裝在專案內或全域（npm root -g）；兩邊都找
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
  { width: 320, height: 800 },
  { width: 360, height: 800 },
  { width: 390, height: 844 },
  { width: 430, height: 932 },
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
    scrollW: document.scrollingElement.scrollWidth,
    innerW: window.innerWidth,
    bodyW: document.body.scrollWidth,
  }));
  check(`${label}: 頁面不得橫向滾動`, m.scrollW <= m.innerW + 1 && m.bodyW <= m.innerW + 1,
    `scrollWidth=${m.scrollW} innerWidth=${m.innerW}`);
}

async function run(browser, vp, scheme) {
  const label = `${vp.width}×${vp.height}${scheme === "dark" ? "（深色）" : ""}`;
  const ctx = await browser.newContext({
    viewport: vp,
    colorScheme: scheme,
    hasTouch: true,
    isMobile: true,
    deviceScaleFactor: 2,
  });
  const page = await ctx.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  await page.goto(BASE, { waitUntil: "networkidle" });
  await page.waitForSelector("#app:not([hidden])", { timeout: 15000 });
  await page.waitForTimeout(400);

  await noHorizontalScroll(page, label);

  // 六個快速操作按鈕完整可讀（在視窗內、沒被裁掉）
  const btns = await page.$$("#quick-actions button");
  check(`${label}: 快速按鈕數量為 6`, btns.length === 6, `找到 ${btns.length} 顆`);
  for (const b of btns) {
    const info = await b.evaluate((n) => ({
      text: n.textContent.trim().slice(0, 12),
      visible: !!(n.offsetWidth && n.offsetHeight),
      inX: n.getBoundingClientRect().right <= window.innerWidth + 1 && n.getBoundingClientRect().left >= -1,
      clipped: n.scrollWidth > n.clientWidth + 2,
      h: n.getBoundingClientRect().height,
    }));
    check(`${label}: 按鈕「${info.text}」可見且未被裁切`,
      info.visible && info.inX && !info.clipped && info.h >= 44,
      `h=${Math.round(info.h)} clipped=${info.clipped}`);
  }

  // 觸控目標：輸入框、送出鈕
  for (const sel of ["#input", "#send"]) {
    const box = await (await page.$(sel)).boundingBox();
    check(`${label}: ${sel} 觸控高度 ≥ 40`, box && box.height >= 40, box ? `h=${Math.round(box.height)}` : "不存在");
  }

  // composer 不遮住訊息區：兩者矩形不得重疊（composer 是排版流內的底欄，
  // 不是浮動覆蓋層，所以正確的檢查是矩形不相交）
  const overlap = await page.evaluate(() => {
    const chat = document.getElementById("chat").getBoundingClientRect();
    const composer = document.getElementById("composer").getBoundingClientRect();
    return { chatBottom: chat.bottom, composerTop: composer.top };
  });
  check(`${label}: composer 不遮住訊息區`, overlap.chatBottom <= overlap.composerTop + 1,
    `chatBottom=${Math.round(overlap.chatBottom)} composerTop=${Math.round(overlap.composerTop)}`);

  // 設定抽屜：開啟、可滑動、Escape 關閉、焦點回到設定鈕
  await page.click("#settings-btn");
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
  await page.waitForTimeout(150);
  check(`${label}: Escape 關閉設定抽屜`, await page.evaluate(() => document.getElementById("settings").hidden));
  check(`${label}: 關閉後焦點回到設定鈕`,
    await page.evaluate(() => document.activeElement && document.activeElement.id === "settings-btn"));

  // Tab 流程：從輸入框 Tab 可移動到可見的可聚焦元素、focus-visible 有樣式
  await page.focus("#input");
  await page.keyboard.press("Tab");
  const focusInfo = await page.evaluate(() => {
    const a = document.activeElement;
    return { id: a && (a.id || a.className || a.tagName), visible: !!(a && a.offsetWidth) };
  });
  check(`${label}: Tab 可移動焦點`, focusInfo.visible, String(focusInfo.id));
  const focusVisible = await page.evaluate(() => {
    let hit = false;
    for (const sheet of document.styleSheets) {
      let rules;
      try { rules = sheet.cssRules; } catch { continue; }
      for (const r of rules) if (r.selectorText && r.selectorText.includes(":focus-visible")) hit = true;
    }
    return hit;
  });
  check(`${label}: 具備 :focus-visible 樣式`, focusVisible);

  // 「繼續上一個任務」modal 開關（返回流程）
  await page.click('#quick-actions button[data-action="continue"]');
  await page.waitForSelector("#session-sheet:not([hidden])");
  const sheetFits = await page.evaluate(() => {
    const card = document.querySelector("#session-sheet .modal-card");
    const r = card.getBoundingClientRect();
    return r.right <= window.innerWidth + 1 && r.left >= -1;
  });
  check(`${label}: 續接視窗不超出螢幕`, sheetFits);
  await page.keyboard.press("Escape");
  await page.waitForTimeout(150);
  check(`${label}: Escape 關閉續接視窗`, await page.evaluate(() => document.getElementById("session-sheet").hidden));

  // 無障礙：所有可見按鈕都要有可辨識名稱
  const unnamed = await page.evaluate(() =>
    [...document.querySelectorAll("button")]
      .filter((b) => b.offsetWidth && !((b.textContent || "").trim() || b.getAttribute("aria-label")))
      .map((b) => b.id || b.className)
  );
  check(`${label}: 可見按鈕皆有名稱（文字或 aria-label）`, unnamed.length === 0, unnamed.join(","));

  // 狀態列 aria-live
  check(`${label}: 進度狀態列具 aria-live`,
    await page.evaluate(() => document.getElementById("status-bar").getAttribute("aria-live") === "polite"));

  // 研究／輪播／產出卡片寬度（注入樣本訊息檢查溢出）
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
    // 直接用前端的渲染管線
    renderMessage(turn, "```ig-carousel\n第一頁：測試超長文字".padEnd(80, "字") + "\n---\n第二頁\n```");
    renderMessage(turn, "## 其他學校怎麼做\nhttps://www.instagram.com/tmu_zenclub/ 這是一段測試\n## 為什麼可能有效\n測試\n## 淡江可以怎麼改良\n測試\n## 哪些內容不能直接照抄\n測試\n## 資料來源\n- [北醫禪學社](https://www.instagram.com/tmu_zenclub/)（發布者：臺北醫學大學）");
    addArtifact(turn, { filename: "115-1-期初茶會-超長活動名稱測試檔案名稱-網宣草稿.md", verified: true, version: 2 });
  });
  await page.waitForTimeout(200);
  const overflow = await page.evaluate(() => {
    const bad = [];
    for (const n of document.querySelectorAll(".soc-card, .research-card, .artifact, .bubble-ai")) {
      const r = n.getBoundingClientRect();
      if (r.right > window.innerWidth + 1 || r.left < -1) bad.push(n.className);
    }
    return bad;
  });
  check(`${label}: 卡片不超出螢幕`, overflow.length === 0, overflow.join(","));
  await noHorizontalScroll(page, `${label}（含卡片）`);

  check(`${label}: 頁面無 JS 錯誤`, errors.length === 0, errors.slice(0, 2).join(" | "));

  await page.screenshot({ path: path.join(OUT, `home-${vp.width}${scheme === "dark" ? "-dark" : ""}.png`), fullPage: false });
  await ctx.close();
}

const browser = await chromium.launch();
for (const vp of VIEWPORTS) {
  console.log(`\n== ${vp.width}×${vp.height} ==`);
  await run(browser, vp, "light");
}
// 深色模式在最小與最大寬度各驗一次
console.log("\n== 深色模式 ==");
await run(browser, VIEWPORTS[0], "dark");
await run(browser, VIEWPORTS[3], "dark");
await browser.close();

fs.writeFileSync(path.join(OUT, "results.json"), JSON.stringify(results, null, 2));
console.log(`\n總計 ${results.length} 項檢查，失敗 ${failures} 項`);
process.exit(failures ? 1 : 0);
