"""Agent evals：用真實社團情境量測代理品質。

設計原則：**核心 evals 不依賴付費外部 API**。
模型回應由 evals/scripted.py 依情境決定，所以 CI 與離線環境都跑得完，
量到的是「代理框架」的品質（意圖分類、skill 路由、工具選擇、檢索相關度、
grounding、幻覺、任務完成、產出有效性、延遲），而不是某個模型當天的手氣。

要量真實模型的話：`python -m evals --live`（需要 NVIDIA_API_KEY）。
"""
