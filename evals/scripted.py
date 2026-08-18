"""給 evals 用的腳本化模型。

它會**看 system prompt 決定怎麼回應**，所以能真實反映代理框架的品質：
  · prompt 說某個欄位「還沒設定」→ 它就照規矩說不知道
  · prompt 有檢索到的歷年內容 → 它會引用
  · prompt 列出預期產出 → 它會呼叫對應的工具

這不是在自問自答造假分數。它模擬的是「一個完全聽話的模型」，
量到的因此是**框架的上限**：如果連完全聽話的模型都會編造今年的事實，
那就是提示與工具設計的問題，換再好的模型也救不回來。

真實模型的表現用 `--live` 量，兩者互補。
"""

from __future__ import annotations

import re
from typing import Any

from app.llm import Reply, ToolCall

# 產出類型 → 工具
TOOL_FOR = {
    "document": "create_document",
    "spreadsheet": "create_spreadsheet",
    "slides": "create_slides",
    "form": "create_google_form",
}

_UNSET = re.compile(r"還沒設定：([^\n。]+)")


def _unset_labels(prompt: str) -> set[str]:
    labels: set[str] = set()
    for m in _UNSET.finditer(prompt):
        labels |= {p.strip() for p in m.group(1).replace("、", ",").split(",") if p.strip()}
    if "完全沒有設定本學期資料" in prompt:
        labels |= {"社長", "幹部", "社課時間", "社課地點", "社費", "招生期間", "報名連結"}
    return labels


def _cited_from_context(prompt: str) -> str:
    """從檢索段落抓一個可引用的來源名稱，用來檢驗 grounding。"""
    m = re.search(r"### 【([^】]+)】([^\n（]+)", prompt)
    return f"{m.group(1)}／{m.group(2).strip()[:40]}" if m else ""


class ScriptedModel:
    """依 system prompt 與案例期望決定回應。"""

    def __init__(self, case, *, break_json: bool = False, drop_args: bool = False):
        self.case = case
        self.break_json = break_json
        self.drop_args = drop_args
        self.turn = 0
        self.model = "scripted/obedient"

    async def chat(self, messages: list[dict[str, Any]], tools=None, **kwargs) -> Reply:
        self.turn += 1
        prompt = next((m["content"] for m in messages if m.get("role") == "system"), "")
        available = {t["function"]["name"] for t in (tools or [])}
        unset = _unset_labels(prompt)

        # 已經被要求修正 → 產出乾淨版本
        last = messages[-1]
        if last.get("role") == "user" and "請修正後" in str(last.get("content") or ""):
            return self._make_artifact(available, clean=True)

        # 純查詢：不產檔，照 prompt 的規矩回答
        if not self.case.artifacts:
            return Reply(content=self._answer(prompt, unset))

        # 第一輪先查歷年範例（如果這個 skill 有這個工具）
        if self.turn == 1 and "search_previous_examples" in available:
            return Reply(
                tool_calls=[
                    ToolCall(
                        id="c_search",
                        name="search_previous_examples",
                        arguments={"query": self.case.message[:40]},
                    )
                ]
            )

        # 產出階段
        if self.turn <= 3:
            return self._make_artifact(available, clean=not self.break_json)

        return Reply(content=self._answer(prompt, unset))

    # ── 回應內容 ─────────────────────────────────────────

    def _answer(self, prompt: str, unset: set[str]) -> str:
        parts: list[str] = []
        blocked = [lbl for lbl in unset if lbl in self.case.message or self._asks_about(lbl)]
        if blocked:
            parts.append(
                f"{'、'.join(sorted(blocked))}這一項本學期還沒設定，我不知道，"
                "請到「本學期設定」補上。我不會用往年的資料代替。"
            )
        cited = _cited_from_context(prompt)
        if cited:
            parts.append(f"（依據：{cited}）")
        if not parts:
            parts.append("依社團知識庫的說明回覆如上。")
        return "\n".join(parts)

    def _asks_about(self, label: str) -> bool:
        alias = {
            "社長": ("社長", "會長"),
            "社課時間": ("時間", "禮拜幾", "星期幾", "幾點"),
            "社課地點": ("地點", "教室", "在哪"),
            "社費": ("社費", "多少錢", "費用"),
            "報名連結": ("報名", "連結", "網址"),
            "招生期間": ("招生期間", "招到什麼時候"),
        }
        return any(w in self.case.message for w in alias.get(label, (label,)))

    def _make_artifact(self, available: set[str], *, clean: bool) -> Reply:
        kind = self.case.artifacts[0] if self.case.artifacts else "document"
        name = TOOL_FOR.get(kind, "create_document")
        if name not in available:
            name = next(
                (TOOL_FOR[k] for k in self.case.artifacts if TOOL_FOR.get(k) in available),
                "create_document",
            )
        if name not in available:
            return Reply(content="這個任務沒有可用的產出工具。")

        title = self.case.message.replace("幫我", "").replace("做", "").strip()[:20] or "產出"

        if self.drop_args and self.turn == 1:
            # 故意漏必要參數，驗證錯誤處理與重試
            return Reply(tool_calls=[ToolCall(id="c_bad", name=name, arguments={"filename": title})])

        if name == "create_spreadsheet":
            args = {
                "filename": title,
                "sheets": [
                    {
                        "name": "主表",
                        "csv": "項目,負責,單價,數量,小計\n海報印製,美宣組,120,10,=C2*D2\n場地佈置,場務組,0,1,=C3*D3",
                    }
                ],
            }
        elif name == "create_google_form":
            args = {
                "filename": title,
                "form_title": title,
                "questions": [
                    {"type": "short_text", "title": "姓名", "required": True},
                    {"type": "multiple_choice", "title": "怎麼知道我們的",
                     "options": ["路宣", "朋友介紹", "IG"]},
                    {"type": "paragraph", "title": "還有什麼想跟我們說的"},
                ],
            }
        elif name == "create_slides":
            args = {
                "filename": title,
                "title": title,
                "slides_markdown": "## 我們是誰\n- 淡江大學領袖禪學社\n\n## 這學期做什麼\n- 每週社課\n- 期初茶會",
            }
        else:
            body = (
                f"# {title}\n\n"
                "## 目標\n\n本次活動目標出席六十人、入社三十人。\n\n"
                "## 流程\n\n報到、體驗禪、手作、社團介紹、凝聚分享。\n\n"
                "## 管道\n\n路宣、個接、網宣、體驗禪。\n\n"
                "## 收入\n\n社費與學校補助。\n\n"
                "## 支出\n\n文宣印製、手作材料、茶點。\n\n"
                "## 社團\n\n淡江大學領袖禪學社\n\n"
                "## 成果\n\n活動結束後統計出席與入社人數。\n\n"
                "## 職掌與交接\n\n各組職掌與交接清單如上。\n\n"
                "## 負責人\n\n社長：待填\n地點：待填\n日期：待填\n"
            )
            if not clean:
                body += "\n禪定可以治療焦慮，保證成功。\n"
            args = {"filename": title, "markdown": body}

        return Reply(tool_calls=[ToolCall(id=f"c_{name}", name=name, arguments=args)])
