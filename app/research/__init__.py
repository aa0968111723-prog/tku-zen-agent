"""Research package facade.

Importing ``app.research`` is side-effect free. Historical public names are
kept through lazy attributes for compatibility; submodules do not initialize
RAG, providers, databases or network clients during package import.
"""

from __future__ import annotations

from importlib import import_module

_LAZY_EXPORTS = {
    "public_map": ("public_map", "*"),
    "HOME_ENTITY_ID": ("entities", "HOME_ENTITY_ID"),
    "ClarificationRequest": ("entities", "ClarificationRequest"),
    "Entity": ("entities", "Entity"),
    "EntityResolution": ("entities", "EntityResolution"),
    "ResearchMode": ("entities", "ResearchMode"),
    "ResearchScope": ("entities", "ResearchScope"),
    "decide_scope": ("entities", "decide_scope"),
    "entity_by_id": ("entities", "entity_by_id"),
    "external_name_lexicon": ("entities", "external_name_lexicon"),
    "resolve": ("entities", "resolve"),
    "ClaimRecord": ("types", "ClaimRecord"),
    "SourceRecord": ("types", "SourceRecord"),
    "classify_source": ("types", "classify_source"),
    "source_from_chunk": ("claims", "source_from_chunk"),
    "AnswerReview": ("verifier", "AnswerReview"),
    "review_answer": ("verifier", "review_answer"),
    "PUBLIC_SOURCE_ENTITIES": ("public_map", "PUBLIC_SOURCE_ENTITIES"),
    "REQUIRED_CATEGORIES": ("public_map", "REQUIRED_CATEGORIES"),
    "is_fetchable_url": ("public_map", "is_fetchable_url"),
}

__all__ = tuple(_LAZY_EXPORTS)


def __getattr__(name: str):
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = import_module(f"{__name__}.{target[0]}")
    value = module if target[1] == "*" else getattr(module, target[1])
    globals()[name] = value
    return value
