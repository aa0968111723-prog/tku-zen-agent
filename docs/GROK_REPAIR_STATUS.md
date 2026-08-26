# Grok 長時間修補狀態

最後更新：2026-08-26T11:20:00Z  
分支：`feat/grok-full-repair`（從 `origin/main` `03e21a7` 建立）  
計畫 PR：https://github.com/aa0968111723-prog/tku-zen-agent/pull/24 （plan-only，不把實作寫進去）

## 目前 phase

Phase 0 審計完成；Phase 1/6：InsForge HTTP 硬化 + 依 `fetch-docs` REST 文件對齊 storage upload-strategy。

## 已完成

- 確認 PR #20–#23 **已 MERGED 進 main**；#24 OPEN、僅 `docs/GROK_FULL_REPAIR_PLAN.md`。
- 架構審計寫入 `docs/GROK_CURRENT_ARCHITECTURE_AUDIT.md`。
- InsForge REST 探測：health 2.3.1、20 張表、private `visual-assets` bucket、知識 517/2430。
- **fetch-docs 等價呼叫**：`GET /api/docs/instructions`（MCP 未掛在此 TUI；同一後端文件端點）。後續拉了 `db/rest-api`、`storage/rest-api`、`functions/rest-api`。
- HTTP adapter：`follow_redirects=False`、circuit breaker、SSRF host check、request id、`Authorization` + `x-api-key`。
- Storage：先 `upload-strategy`，direct/404 則 PUT fallback；presigned 不帶 InsForge Authorization。

## 進行中

- 安裝 pytest 並跑 HTTP hardening + 既有 InsForge 測試。
- 後續缺陷掃描修補、0006 僅在確認缺欄位時才加。

## Blockers

| 項目 | 代碼 | 說明 |
|---|---|---|
| InsForge MCP 未掛在此 Grok TUI | `BLOCKED_BY_EXTERNAL_DEPENDENCY` | 已用 `GET /api/docs/instructions` 取代 `fetch-docs`。請在本機 Cursor 掛 MCP 才能用 `run-raw-sql`。 |
| Windows 素材樹不在此 Linux runner | `BLOCKED_BY_EXTERNAL_DEPENDENCY` | `淡大劇本/場景` 等路徑不存在；不假裝已匯入 29GB。 |
| fal Vision / OCR | 未設定則既有測試已覆蓋 blocker | 不造假 PASS。 |
| 使用者在聊天貼了 InsForge API key | 安全 | **請在 InsForge 後台輪替**。金鑰未寫入 Git。 |

## 下一步

1. 跑新增 HTTP / storage-strategy 測試與既有 pytest。
2. 掃描 P0 缺陷（path traversal、跨 user）。
3. 開 implementation PR（不 merge、不 force push）。
