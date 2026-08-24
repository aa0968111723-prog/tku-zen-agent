"""研究驗證引擎：實體解析、來源證據模型、回答品質閘門。

這一層回答三個問題，缺一不可才能宣稱「研究完成」：

  1. 研究對象是誰？（entities —— 不確定就反問，不猜）
  2. 證據是什麼？（claims —— 沒有 source_url/title/excerpt 不得標 verified）
  3. 回答有沒有超出證據？（verifier —— 混淆、冒充、推測當事實，一律擋下）
"""

from .entities import (  # noqa: F401
    HOME_ENTITY_ID,
    ClarificationRequest,
    Entity,
    EntityResolution,
    ResearchMode,
    ResearchScope,
    decide_scope,
    entity_by_id,
    external_name_lexicon,
    resolve,
)
from .claims import (  # noqa: F401
    ClaimRecord,
    SourceRecord,
    classify_source,
    source_from_chunk,
)
from .verifier import AnswerReview, review_answer  # noqa: F401
