# Grok 長時間修補狀態

最後更新：2026-08-26T13:40:00Z  
分支：`feat/grok-full-repair`（從 `origin/main` `03e21a7` 建立）  
計畫 PR：https://github.com/aa0968111723-prog/tku-zen-agent/pull/24 （plan-only，不把實作寫進去）

## 目前 phase

Phase 0 完成；Phase 1 缺陷掃描進行中並已修 P0/P1：路徑、重啟覆寫 review_status、搜尋 limit、分析讀檔、HTTP 不跟隨轉址。

## 已完成

- 確認 PR #20–#23 **已 MERGED 進 main**；#24 OPEN、僅 `docs/GROK_FULL_REPAIR_PLAN.md`。
- 架構審計寫入 `docs/GROK_CURRENT_ARCHITECTURE_AUDIT.md`。
- InsForge REST 探測：health 2.3.1、20 張表、private `visual-assets` bucket、知識 517/2430。
- **fetch-docs 等價呼叫**：`GET /api/docs/instructions`（MCP 未掛在此 TUI；同一後端文件端點）。後續拉了 `db/rest-api`、`storage/rest-api`、`functions/rest-api`。
- HTTP adapter：`follow_redirects=False`、circuit breaker、SSRF host check、request id、`Authorization` + `x-api-key`。
- Storage：先 `upload-strategy`，direct/404 則 PUT fallback；presigned 不帶 InsForge Authorization。
- P0：本地 `asset_file` 限制在 `VISUAL_ASSET_DIR`（external original 仍可在原位）；`create_asset` 寫入 `project_id`/`owner_id`；視覺同步讀 `backend_user_mappings`。
- Phase 1：重啟中斷分析 **不再** 把 `review_status` 改成 failed；Vision 讀檔走 `asset_file` 限制；搜尋/列表 `limit` 上限 100；fal/LLM `follow_redirects=False`；背景分析失敗會寫 log 而非完全吞掉。

## 進行中

- Phase 1 其餘缺陷（worker 續跑、跨表 review_queue 讀取）仍為已知平行實作，不重建。
- 0006 僅在確認缺欄位時才加；目前現有 0001–0005 已夠用。

## Blockers

| 項目 | 代碼 | 說明 |
|---|---|---|
| InsForge MCP 未掛在此 Grok TUI | `BLOCKED_BY_EXTERNAL_DEPENDENCY` | 已用 `GET /api/docs/instructions` 取代 `fetch-docs`。請在本機 Cursor 掛 MCP 才能用 `run-raw-sql`。 |
| Windows 素材樹不在此 Linux runner | `BLOCKED_BY_EXTERNAL_DEPENDENCY` | `淡大劇本/場景` 等路徑不存在；不假裝已匯入 29GB。 |
| fal Vision / OCR | 未設定則既有測試已覆蓋 blocker | 不造假 PASS。 |
| 使用者在聊天貼了 InsForge API key | 安全 | **請在 InsForge 後台輪替**。金鑰未寫入 Git。 |

## 下一步

1. 繼續 Phase 1 剩餘項目（background retry scanner 需新 worker，暫不新增 Redis）。
2. Windows 素材樹仍不存在，Phase 3/4 匯入保持 blocker。
3. Implementation PR：https://github.com/aa0968111723-prog/tku-zen-agent/pull/25 （不 merge）。
