"""Shared candidate retrieval for centralized dispatch and relay search.

This module only retrieves and ranks catalog candidates. It does not decide
HIT_SINGLE/HIT_MULTI_BOTS/MISS, apply claim-join filtering, or select BBS.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from agentclaw.community.core.task.domain.identity import compose_bot_identity

logger = logging.getLogger("task.candidate.search")

MAX_SEARCH_TOKENS = 5
TOP_K_PER_TOKEN = 3
MIN_SCORE = 0.01
DEFAULT_FILTERS = {"runtime_state": ["online"]}

_STOPWORDS: frozenset[str] = frozenset({
    "可以", "需要", "能够", "应该", "应当", "必须", "可能", "想要",
    "以及", "并且", "或者", "但是", "然而", "如果", "由于", "虽然",
    "而且", "不仅", "只要", "只有", "因此", "所以", "然后", "接着", "此外",
    "这种", "这样", "那种", "那些", "这些", "我们", "他们", "你们", "它们",
    "一个", "没有", "已经", "进行", "通过", "对于", "关于", "根据", "按照", "基于", "同时",
    "之前", "之后", "现在", "目前", "之间", "以上", "以下", "一些", "某种", "只是", "还是",
    "就是", "不是", "不能", "不要", "成为", "作为", "其中", "其它", "另外", "比如", "例如",
    "产出", "提供", "给出", "做出", "得到", "形成", "构成", "具备", "包含", "包括",
    "涉及", "带来", "产生", "梳理", "整理", "归纳", "总结", "概述", "阐述", "说明",
    "描述", "列举", "呈现", "展示", "列出", "拟定", "制定", "建立", "不少", "若干",
    "针对", "围绕", "结合",
})


@dataclass(frozen=True)
class CandidateSearchResult:
    """Retrieval facts used for diagnostics; no dispatch decision is made here."""

    candidates: list[dict[str, Any]]
    tokens: list[str]
    raw_item_count: int
    failed_keywords: list[str]


def tokenize_query(text: str) -> list[str]:
    """Split text into semantic keywords, with the existing fallback behavior."""
    if not text:
        return []
    try:
        import jieba  # type: ignore[import-untyped]
    except ImportError:
        words = [text]
    else:
        words = jieba.cut(text)
    return [word for word in words if len(word.strip()) >= 2 and word not in _STOPWORDS]


def search_tokens(text: str, *, max_tokens: int = MAX_SEARCH_TOKENS) -> list[str]:
    """Deduplicate tokenized query in order and cap the number of searches."""
    tokens: list[str] = []
    seen: set[str] = set()
    for token in tokenize_query(text or ""):
        if token not in seen:
            seen.add(token)
            tokens.append(token)
    return tokens[:max_tokens]


def _candidate_identity(item: dict[str, Any]) -> str:
    bot_uuid = str(item.get("bot_uuid") or "").strip()
    if bot_uuid:
        return bot_uuid
    return compose_bot_identity(str(item.get("bot_id") or ""), item.get("owner_id"))


def _score(item: dict[str, Any]) -> float:
    value = (item.get("recommend") or {}).get("score", 0.0)
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


async def search_candidates(
    discover,
    query: str,
    *,
    user_id: str = "",
    top_k_per_token: int = TOP_K_PER_TOKEN,
    max_tokens: int = MAX_SEARCH_TOKENS,
    min_score: float = MIN_SCORE,
    filters: dict[str, Any] | None = None,
) -> CandidateSearchResult:
    """Search the same tokenized catalog candidate set for every task mode."""
    tokens = search_tokens(query, max_tokens=max_tokens)
    if not tokens or discover is None:
        return CandidateSearchResult([], tokens, 0, [])

    search_filters = filters if filters is not None else DEFAULT_FILTERS

    async def _query(keyword: str) -> tuple[str, list[dict[str, Any]], bool]:
        try:
            result = await asyncio.to_thread(
                discover.search_by_keyword,
                keyword=keyword,
                user_id=user_id,
                top_k=top_k_per_token,
                min_score=min_score,
                filters=search_filters,
            )
        except Exception as exc:  # noqa: BLE001 - one keyword must not block others
            logger.warning(
                "keyword_search_failed keyword=%r error_type=%s error=%s",
                keyword,
                type(exc).__name__,
                str(exc)[:500],
                exc_info=True,
            )
            return keyword, [], True
        items = result.get("items") if isinstance(result, dict) else []
        return keyword, items if isinstance(items, list) else [], False

    responses = await asyncio.gather(*[_query(token) for token in tokens])
    seen: dict[str, dict[str, Any]] = {}
    raw_item_count = 0
    failed_keywords: list[str] = []
    for keyword, items, failed in responses:
        if failed:
            failed_keywords.append(keyword)
        if not items:
            # Empty results are valid misses, so only failures are tracked by the
            # keyword helper through the logger; the API remains candidate-only.
            continue
        raw_item_count += len(items)
        for item in items:
            if not isinstance(item, dict):
                continue
            identity = _candidate_identity(item)
            if identity and identity not in seen:
                seen[identity] = item
    candidates = sorted(seen.values(), key=_score, reverse=True)
    return CandidateSearchResult(candidates, tokens, raw_item_count, failed_keywords)
