"""上傳產出到 Google 雲端硬碟（選用）。

兩種認證方式：
  · OAuth 使用者授權 —— 本機跑的時候用。第一次會開瀏覽器要你登入，
    之後 token 存在 data/google_token.json，不用再登入。
  · 服務帳號 —— 部署到 Zeabur 之類的伺服器時用（伺服器沒有瀏覽器可以開）。
    把服務帳號的 JSON 放到 GOOGLE_CREDENTIALS_FILE，並把目標資料夾
    分享給該服務帳號的信箱。

沒設定的話，上傳會失敗但不影響本機產出 —— 檔案照樣存在 outputs 資料夾。
"""

from __future__ import annotations

import json
import mimetypes
from pathlib import Path

from .. import config

SCOPES = ["https://www.googleapis.com/auth/drive.file"]

# 上傳時要不要轉成 Google 原生格式（試算表/文件/簡報）
CONVERT = {
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "application/vnd.google-apps.spreadsheet",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "application/vnd.google-apps.document",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "application/vnd.google-apps.presentation",
}

_service = None


def _build_service():
    global _service
    if _service is not None:
        return _service

    try:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise RuntimeError(
            "沒有安裝 Google 套件。請執行："
            "pip install google-api-python-client google-auth-oauthlib google-auth-httplib2"
        ) from exc

    cred_file: Path = config.GOOGLE_CREDENTIALS_FILE
    token_file: Path = config.GOOGLE_TOKEN_FILE

    if not cred_file.exists():
        raise RuntimeError(
            f"找不到 Google 憑證檔 {cred_file}。"
            "請照 README 的「雲端落點設定」建立，或把落點改成 local。"
        )

    raw = json.loads(cred_file.read_text(encoding="utf-8"))

    # 服務帳號
    if raw.get("type") == "service_account":
        from google.oauth2 import service_account

        creds = service_account.Credentials.from_service_account_file(str(cred_file), scopes=SCOPES)
        _service = build("drive", "v3", credentials=creds, cache_discovery=False)
        return _service

    # OAuth 使用者授權
    from google.auth.transport.requests import Request
    from google_auth_oauthlib.flow import InstalledAppFlow

    creds = None
    if token_file.exists():
        creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(cred_file), SCOPES)
            creds = flow.run_local_server(port=0, prompt="consent")
        token_file.parent.mkdir(parents=True, exist_ok=True)
        token_file.write_text(creds.to_json(), encoding="utf-8")

    _service = build("drive", "v3", credentials=creds, cache_discovery=False)
    return _service


def upload(path: Path, mime: str | None = None, folder_id: str | None = None) -> str:
    """上傳單一檔案，回傳可開啟的連結。"""
    from googleapiclient.http import MediaFileUpload

    service = _build_service()
    mime = mime or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    target = folder_id or config.DRIVE_FOLDER_ID

    metadata: dict = {"name": path.name}
    if target:
        metadata["parents"] = [target]
    if mime in CONVERT:
        metadata["mimeType"] = CONVERT[mime]

    media = MediaFileUpload(str(path), mimetype=mime, resumable=False)
    created = (
        service.files()
        .create(body=metadata, media_body=media, fields="id,webViewLink", supportsAllDrives=True)
        .execute()
    )
    return created.get("webViewLink") or f"https://drive.google.com/file/d/{created['id']}/view"


def is_configured() -> bool:
    return config.GOOGLE_CREDENTIALS_FILE.exists()
