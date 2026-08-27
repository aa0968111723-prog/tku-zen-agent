# 淡江公開資訊與 Instagram 公開搜尋

本功能把淡江官方公開網站與 Meta Instagram 公開 hashtag 搜尋接到既有 agent tool registry。它不是淡江大學官方 MCP，也不會取得私人帳號權限。

## 已接入的 agent tools

| Tool | 用途 |
| --- | --- |
| list_tku_public_sources | 列出白名單內的淡江官方公開來源 |
| search_tku_public_info | 讀取白名單來源並抽取可搜尋文字 |
| fetch_tku_public_source | 讀取指定官方來源，含網域白名單與雜湊 |
| search_instagram_public_hashtag | 透過 Meta Graph API 搜尋授權範圍內的公開 hashtag 內容 |
| search_instagram_public_account | 讀取指定公開專業帳號的公開資料與貼文 |

## 安全邊界

- 只讀取 HTTPS 與淡江官方網域白名單。
- Instagram token 只在 server-side 設定；不放入 tool schema、system prompt 或前端回應。
- 不抓私人帳號、不繞過登入、不用爬蟲模擬個人帳號、不猜測照片中的真實人物。
- 外部公開內容必須保留來源、擷取時間與內容雜湊，且不能直接標記為淡江已確認事實。
- API endpoint 仍受既有使用者認證保護；未來若寫入知識庫，必須再套用 project/source ACL 與人工 review。

## Meta 設定

在部署環境設定：

~~~dotenv
INSTAGRAM_APP_ID=你的_Meta_App_ID
INSTAGRAM_ACCESS_TOKEN=server_only_token
INSTAGRAM_BUSINESS_ACCOUNT_ID=已連結的專業帳號_ID
INSTAGRAM_GRAPH_API_VERSION=v21.0
INSTAGRAM_PUBLIC_SEARCH_ENABLED=true
INSTAGRAM_API_TIMEOUT_SECONDS=15
~~~

Instagram 公開 hashtag 搜尋需要 Meta App Review 與相應權限。未完成審核或未開啟設定時，tool 會回傳 BLOCKED_BY_EXTERNAL_DEPENDENCY，不會假裝成功。請另外遵守 Meta 對 hashtag 搜尋數量、速率與資料保存的限制。

## API

- GET /api/public-sources
- POST /api/public-sources/search
- GET /api/public-sources/{source_id}
- POST /api/instagram/public/hashtag-search
- POST /api/instagram/public/account-search

以上 endpoint 會使用既有登入保護；Instagram 搜尋不會提供發文、留言、私訊或私人資料能力。
