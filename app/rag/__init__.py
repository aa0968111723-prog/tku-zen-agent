"""RAG package facade with lazy compatibility exports."""

from __future__ import annotations

from importlib import import_module

_LAZY_EXPORTS = {
    "ContextBundle": ("context", "ContextBundle"),
    "build_context": ("context", "build_context"),
    "ChunkMeta": ("metadata", "ChunkMeta"),
    "infer": ("metadata", "infer"),
    "ParsedQuery": ("query", "ParsedQuery"),
    "parse": ("query", "parse"),
    "hybrid_search": ("hybrid", "hybrid_search"),
}

__all__ = tuple(_LAZY_EXPORTS)


def __getattr__(name: str):
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(f"{__name__}.{target[0]}"), target[1])
    globals()[name] = value
    return value
