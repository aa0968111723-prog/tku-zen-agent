"""查本學期真實資料的工具。

跟 search_knowledge 分開的理由：知識庫查到的東西可能是 108 學年度的，
當期資料必須有一個明確、不會誤會的來源。
"""

from __future__ import annotations

from ..services import current_term as term


def get_current_term(keys: str = "") -> dict:
    state = term.load()
    known = state.known()

    wanted: list[str] = []
    if keys:
        for raw in str(keys).replace("，", ",").split(","):
            k = raw.strip()
            if k in term.FIELD_BY_KEY:
                wanted.append(k)
            else:
                # 也接受中文標籤
                for f in term.FIELDS:
                    if k and (k == f.label or k in f.label):
                        wanted.append(f.key)
                        break
    if not wanted:
        wanted = [f.key for f in term.FIELDS]

    lines: list[str] = [f"本學期：{state.term_label()}"]
    unknown: list[str] = []
    for key in wanted:
        field = term.FIELD_BY_KEY[key]
        value = known.get(key)
        if value:
            lines.append(f"- {field.label}：{value}")
        else:
            unknown.append(field.label)

    if unknown:
        lines.append("")
        lines.append("以下**還沒設定**：" + "、".join(unknown))
        lines.append(
            "這些資料你不知道。不可以從歷年檔案、去年資料或常識推測一個看起來合理的值。"
            "使用者問就告訴他還沒設定、請他到「本學期設定」填寫；"
            "做檔案就把該欄位填「待填」。"
        )

    return {
        "ok": True,
        "message": "\n".join(lines),
        "known_keys": [k for k in wanted if k in known],
        "unknown_keys": [k for k in wanted if k not in known],
    }


SCHEMA = {
    "type": "function",
    "function": {
        "name": "get_current_term",
        "description": (
            "查本學期（今年）的真實資料：學年度、學期、社長、幹部、社課時間地點、"
            "社費、招生期間、本學期週次表、報名連結、共用雲端資料夾。"
            "**只要問題牽涉到「今年」「這學期」「現在」的具體事實，就一定要先呼叫這個工具**，"
            "不可以用 search_knowledge 查到的歷年檔案內容代替 —— 那些是往年的資料。"
            "查不到就是還沒設定，直接說明並請使用者補，絕對不要自己編一個。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "keys": {
                    "type": "string",
                    "description": (
                        "想查哪些欄位，用逗號分隔；留空就全部回傳。"
                        "可用：academic_year, semester, club_name, president, officers, "
                        "regular_meeting_time, regular_meeting_location, club_fee, "
                        "recruitment_period, weekly_schedule, signup_url, primary_drive_folder, source_note"
                    ),
                }
            },
        },
    },
}
