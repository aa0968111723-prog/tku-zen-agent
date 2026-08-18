"""可腳本化的假模型。

測試與 evals 都用它，所以整套測試不需要 NVIDIA API 金鑰就能跑完。
支援三種腳本形式：
  · Reply 物件         —— 完全指定回覆
  · callable(messages) —— 依當下對話動態決定，用來測多輪行為
  · str                —— 純文字回覆的簡寫
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

from app.llm import LLMError, Reply, ToolCall

Script = Reply | str | Callable[[list[dict[str, Any]]], "Reply | str"]


def tool(name: str, **arguments: Any) -> Reply:
    """腳本化一次工具呼叫。"""
    return Reply(tool_calls=[ToolCall(id=f"call_{name}", name=name, arguments=arguments)])


def say(text: str) -> Reply:
    return Reply(content=text)


@dataclass
class FakeLLM:
    """依序吐出腳本內容；腳本用完就重複最後一個。"""

    script: list[Script] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)
    raise_after: int | None = None
    model: str = "fake/model"

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> Reply:
        idx = len(self.calls)
        self.calls.append(
            {
                "messages": messages,
                "tools": [t["function"]["name"] for t in (tools or [])],
                "kwargs": kwargs,
            }
        )
        if self.raise_after is not None and idx >= self.raise_after:
            raise LLMError("測試用的模擬失敗")
        if not self.script:
            return Reply(content="（沒有腳本）")
        item = self.script[min(idx, len(self.script) - 1)]
        if callable(item):
            item = item(messages)
        if isinstance(item, str):
            return Reply(content=item)
        return item

    # ── 給測試斷言用的輔助 ──────────────────────────────────

    @property
    def call_count(self) -> int:
        return len(self.calls)

    def tools_offered(self, call_index: int = 0) -> list[str]:
        return self.calls[call_index]["tools"]

    def last_user_visible(self) -> str:
        """最後一次呼叫時，送給模型的所有內容串起來（用來檢查有沒有夾帶東西）。"""
        return "\n".join(str(m.get("content") or "") for m in self.calls[-1]["messages"])

    def system_prompt(self, call_index: int = 0) -> str:
        for m in self.calls[call_index]["messages"]:
            if m.get("role") == "system":
                return str(m.get("content") or "")
        return ""

    def tool_results(self) -> list[dict[str, Any]]:
        """從最後一次呼叫的 messages 裡撈出所有 tool 回傳。"""
        out = []
        for m in self.calls[-1]["messages"]:
            if m.get("role") == "tool":
                try:
                    out.append(json.loads(m["content"]))
                except (json.JSONDecodeError, KeyError):
                    out.append({"raw": m.get("content")})
        return out
