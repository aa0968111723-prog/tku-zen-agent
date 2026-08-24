# Security Policy — tku-zen-agent

本文件說明淡江大學禪學社／領袖禪學社 AI 工作台的安全基線。
實作以「最小權限、人工確認後發布、Token 後端保管、個資最小化」為原則。

## 1. Scope

- 後端 API（FastAPI）
- 前端靜態介面
- 知識庫／RAG
- OAuth／社群 API（若啟用）
- 日誌、SQLite 持久化、產出檔

## 2. Threat Model（摘要）

| 風險 | 對應 | 對策摘要 |
|------|------|----------|
| Prompt Injection | OWASP LLM01:2026 | 系統提示與使用者／RAG 分離；工具白名單 |
| Sensitive Information Disclosure | OWASP LLM02 | Token／路徑不進前端與 log |
| Excessive Agency | OWASP LLM03 | 對外發布強制 HITL；禁止自動刪改官方資料 |
| Data / RAG poisoning | OWASP LLM05 | 來源分級、外部內容不可覆寫 current_term |
| Token 洩漏與重放 | RFC 9700 | 後端持有、短生命週期、不記錄明文 |
| 個資外洩 | 個資法 | 最小化、可刪除、遮罩 |

## 3. Authentication & OAuth（RFC 9700）

- 部署模式使用 `APP_ACCESS_TOKEN` 換取身分 cookie（httponly）
- 管理者另需 `ADMIN_ACCESS_TOKEN`
- 未來 Instagram／Meta 整合採 **Authorization Code + PKCE (S256)**
- Redirect URI 精確比對；禁止 Implicit／Password Grant
- Access／Refresh Token 僅存伺服器端；Refresh 建議旋轉

## 4. Authorization & Roles

| 角色 | 說明 |
|------|------|
| `guest` | 未登入，僅公開頁面 |
| `user` | 一般授權使用者：Ask／草稿／自己的 session 與 artifact |
| `editor` | 幹部：可提交待審產出（HITL 後續 PR） |
| `admin` | 管理者：term、reindex、管理 API |
| `system` | 內部背景工作，不得代表使用者對外發布 |

高風險動作（正式對外發布、刪除大量資料、改 current_term）必須人工確認或僅限 admin，且寫入 audit log。

## 5. AI Agent Controls

- 工具呼叫經白名單與參數驗證
- RAG 內容視為不可信輸入，標來源、不可覆寫官方事實
- 禁止代理自動：對外發布、刪使用者／官方資料、未授權匯出個資
- Instagram 相關端點在未審核通過前維持 501／草稿模式

## 6. Human-in-the-Loop

- 對外發布前狀態應進入待確認（後續 PR 實作 `WAITING_HUMAN`）
- UI 需區分草稿與已核准

## 7. Data Protection（個人資料保護法）

- 特定目的、蒐集最小化、告知義務
- 當事人得請求查詢、更正、刪除、停止利用
- `current_term.yaml` 等含姓名之檔不進版控（僅 example）
- 私訊／留言若納入系統：最短必要保存、不可用於無關訓練

## 8. Knowledge Base & RAG Hygiene

- 來源分級（官方 > 已驗證公開 > 未驗證）
- 去重、過期、`last_verified_at`（後續 PR）
- AI 推論與原始事實分離

## 9. Secrets & Tokens

- 密鑰不進 git、不進前端、不進明文 log
- 使用 `app.services.audit.mask_secrets` 遮罩 token 片段
- Instagram／Google 憑證僅伺服器端

## 10. Audit Logging

- 表：`audit_logs`（append-only）
- 記錄：登入成敗、admin 操作、term 變更、reindex、發布嘗試等
- 欄位含 `actor_user_id`、`action`、`resource`、`detail`（已遮罩）、`ip`、`created_at`
- 不記錄完整授權碼或 Access Token

## 11. Secure Development

- 依賴與測試見 `tests/`
- 下載端點以 artifact_id + 歸屬檢查，禁止任意 path
- 錯誤訊息不暴露堆疊與密鑰

## 12. Incident Response

- Token 外洩：立即輪替 `APP_ACCESS_TOKEN`／`ADMIN_ACCESS_TOKEN` 與社群 token
- 個資事件：依個資法第 12 條評估通知與留存紀錄
- 錯誤發布：下架並以 audit log 追查批准者

## 13. Accessibility（WCAG 2.2）

- 登入與確認等安全操作可鍵盤完成、焦點可見、狀態有文字說明

## 14. Reporting a Vulnerability

請透過 repository 維護者私下回報，勿在公開 issue 貼上 token 或個資。

## 15. References

- RFC 9700 — OAuth 2.0 Security Best Current Practice
- OWASP GenAI LLM Top 10 2026
- 個人資料保護法（全國法規資料庫 PCode=I0050021）
- WCAG 2.2
- Meta Instagram Platform 政策（若啟用官方 API）
