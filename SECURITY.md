# Security Policy — tku-zen-agent

## 1. Scope

社團營運輔助 AI Agent：查資料、規劃活動、產網宣、整理社評、任務進度、**人工確認後**發布。  
涵蓋 API、前端、RAG／知識庫、OAuth、社群 API、日誌與儲存。

## 2. Threat Model（摘要）

- Prompt Injection（直接／間接／跨模態）
- Excessive Agency（過度代理）
- Token 洩漏與重放
- 外部社群資料污染知識庫
- 個資外洩與超目的利用
- 未授權對外發布

## 3. Authentication & OAuth（RFC 9700）

- 部署模式使用共享存取碼換取身分 cookie（httponly；token 模式 Secure）
- 未來社群 OAuth 採 Authorization Code + PKCE (S256)
- Token／密鑰僅後端持有；禁止進入前端、git、明文 log
- 禁止 Implicit / Resource Owner Password Grant
- 環境分離（本機 local / 部署 token）

## 4. Authorization & Roles

| 角色 | 說明 |
|------|------|
| Guest | 未登入 |
| User | 一般授權使用者 |
| Editor | 幹部（預留；目前與 User 同等，後續可細分） |
| Admin | 管理者（term／reindex／IG 管理端點） |
| System | 內部背景作業 |

高風險動作（對外發布、改 term、reindex）強制 Admin 或後續 HITL。

## 5. AI Agent Controls（OWASP LLM Top 10 2026）

- 系統提示與使用者／RAG 輸入隔離
- 工具白名單與參數驗證
- RAG 標來源；外部內容不可覆寫官方事實
- 禁止自動：對外發布、刪資料、改 term、大量匯出個資

## 6. Human-in-the-Loop

- 對外發布必須人工確認（後續 PR 強化 `WAITING_HUMAN`）
- Instagram 寫入端點目前為 Admin + 未啟用（501）

## 7. Data Protection（個人資料保護法）

- 特定目的、最小化、告知義務
- 當事人：查詢、更正、刪除、停止利用（流程待產品化）
- `current_term.yaml` 含個資，不進版控

## 8. Knowledge Base & RAG Hygiene

- 來源分級與外部參考 opt-in
- AI 推論與原始事實分離（後續 PR 強化）

## 9. Secrets & Tokens

- `APP_ACCESS_TOKEN`、`ADMIN_ACCESS_TOKEN`、`NVIDIA_API_KEY`、Instagram／Google 憑證不進 git
- 日誌使用遮罩（見 `app.services.security.mask_secret`）

## 10. Audit Logging

- 登入成功／失敗、管理動作、敏感 API 寫入 `audit_logs`
- 含 action、actor、role、resource、request 維度、時間
- 不記錄完整 token 或密碼

## 11. Secure Development

- 輸入驗證（訊息長度等）
- Artifact 下載驗 user_id 與路徑沙箱
- 既有測試：`tests/test_api_security.py`、`tests/test_architecture_security.py`、`tests/test_audit.py`

## 12. Incident Response

- Token 外洩：輪替環境變數、清 cookie、檢查 audit
- 個資事件：依個資法第 12 條評估通知

## 13. Accessibility（WCAG 2.2）

- 登入與安全操作應可鍵盤完成、狀態有文字說明（前端後續強化）

## 14. Reporting a Vulnerability

請透過 repository 維護者私訊回報，勿公開貼出 token 或個資。

## 15. References

- RFC 9700 — OAuth 2.0 Security BCP
- OWASP GenAI LLM Top 10 2026
- 個人資料保護法（PCode=I0050021）
- WCAG 2.2
