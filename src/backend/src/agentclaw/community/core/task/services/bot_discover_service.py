"""BotDiscoverPort community impl — generalized搜推 (Phase 4.1, plan §2.4).

"搜推 bot" 是泛化语义:不只是找单个 bot,而是发现能 100% cover 子需求的
执行方——**单 bot / 协作群(多 bot 拼合)/ 不可完成**——并产出
``RouteRecommendation(route_class, run_mode, candidates, confidence)`` 供
Scheduler ``_route`` 决策。非 bcsfuse 依赖:cover 计算用本地 ``BotCatalogPort``
(社区 ``LocalBotCatalog``,真实 bot 源经 DI 注入)。

route_class 判定(对齐 ``RouteClass``):
- C1 单 bot 全 cover(cover == 1.0)→ ``SINGLE_BOT``
- C2 spec 空泛需澄清 → ``SINGLE_BOT`` + confidence 0.0
- C3 协作群:多 bot 拼合 union 全 cover → ``COOP_GROUP``
- C4 部分 cover(0 < cover < 1.0),需运行期拆解 → ``SINGLE_BOT`` placeholder
- C5 零 cover(无 bot 能沾边)→ ``BBS`` 上升

attempted_executors **不在 recommend 排除**(P10 降权在 Scheduler._route,见
plan §2.2);recommend 只按 cover 排候选。
"""
from __future__ import annotations

from typing import Optional

from agentclaw.community.core.task.domain.models import Node, RunMode, Task
from agentclaw.community.core.task.domain.repository import TaskRepo
from agentclaw.community.core.task.protocols import (
    BotCandidate,
    BotDiscoverPort,
    RouteClass,
    RouteRecommendation,
)
from agentclaw.community.core.task.services.bot_catalog import (
    BotCatalogPort,
    BotProfile,
    LocalBotCatalog,
)

_FULL_COVER = 1.0
_EPS = 1e-9
# English stopwords only for the P0 heuristic; spec text is lowercased + tokenized.
_STOPWORDS = frozenset(
    {"a", "an", "the", "and", "or", "but", "of", "to", "in", "on", "for", "with",
     "is", "are", "be", "it", "this", "that", "do", "does", "need", "needs",
     "please", "help", "want", "using", "use", "by", "at", "as", "into",
     # generic action verbs — cover should match domain nouns/skills, not filler verbs
     "implement", "write", "build", "create", "make", "run", "produce",
     "deliver", "fix", "update", "add", "setup", "set", "configure", "generate",
     "develop", "design", "draft", "publish"}
)


def _tokenize(text: str) -> list[str]:
    return [t for t in (text or "").lower().replace(".", " ").replace(",", " ")
            .replace(";", " ").replace(":", " ").replace("/", " ")
            .replace("-", " ").split() if t]


def _keywords(text: str) -> list[str]:
    """Lowercased meaningful tokens, deduped (order-preserving), stopword-filtered."""
    seen: set[str] = set()
    out: list[str] = []
    for tok in _tokenize(text):
        if len(tok) < 2 or tok in _STOPWORDS or tok in seen:
            continue
        seen.add(tok)
        out.append(tok)
    return out


def _bot_capability_tokens(bot: BotProfile) -> set[str]:
    """A bot's capability vocabulary = skills + summary tokens.

    Skills are tokenized with the same splitter as node keywords so a
    hyphenated multi-word skill (e.g. ``code-review``) matches single-word node
    keywords (``code``, ``review``); bare lowercased skills are also kept for
    exact single-token skills (e.g. ``python``).
    """
    caps: set[str] = set()
    for s in bot.skills:
        if not s:
            continue
        sl = s.lower()
        caps.add(sl)
        caps.update(_tokenize(sl))
    caps.update(_tokenize(bot.summary))
    return caps


def _cover(node_keywords: list[str], bot: BotProfile) -> float:
    if not node_keywords:
        return 0.0
    caps = _bot_capability_tokens(bot)
    hit = sum(1 for kw in node_keywords if kw in caps)
    return hit / len(node_keywords)


def _greedy_set_cover(node_keywords: list[str], bots: list[BotProfile]) -> list[BotProfile]:
    """Greedy minimal set of bots whose capability union covers all node keywords."""
    remaining = set(node_keywords)
    selected: list[BotProfile] = []
    available = list(bots)
    while remaining and available:
        best = max(available, key=lambda b: len(remaining & _bot_capability_tokens(b)))
        gain = len(remaining & _bot_capability_tokens(best))
        if gain == 0:
            break
        selected.append(best)
        remaining -= _bot_capability_tokens(best)
        available.remove(best)
    return selected


def _union_cover(node_keywords: list[str], selected: list[BotProfile]) -> float:
    if not node_keywords:
        return 0.0
    union: set[str] = set()
    for b in selected:
        union |= _bot_capability_tokens(b)
    hit = sum(1 for kw in node_keywords if kw in union)
    return hit / len(node_keywords)


class BotDiscoverService(BotDiscoverPort):
    """Generalized搜推 impl. Reads node spec text via the injected ``TaskRepo``
    (read-only); ``recommend_for_spec`` is the pure core, unit-testable without a repo.
    """

    def __init__(
        self,
        task_repo: Optional[TaskRepo] = None,
        bot_catalog: Optional[BotCatalogPort] = None,
    ) -> None:
        self._task_repo = task_repo
        self._bot_catalog = bot_catalog if bot_catalog is not None else LocalBotCatalog()

    def recommend(self, task_id: str, node_id: str) -> RouteRecommendation:  # type: ignore[override]
        spec_text = self._load_node_spec(task_id, node_id)
        return self.recommend_for_spec(spec_text)

    def recommend_for_spec(self, spec_text: str) -> RouteRecommendation:
        node_kw = _keywords(spec_text)
        bots = self._bot_catalog.list_bots()

        if not node_kw:
            return RouteRecommendation(
                route_class=RouteClass.C2,
                run_mode=RunMode.SINGLE_BOT,
                candidates=[],
                confidence=0.0,
                rationale="spec empty/ambiguous — needs clarification",
            )

        if not bots:
            return RouteRecommendation(
                route_class=RouteClass.C5,
                run_mode=RunMode.BBS,
                candidates=[],
                confidence=0.0,
                rationale="no bots available — escalate to BBS",
            )

        scored = sorted(((b, _cover(node_kw, b)) for b in bots), key=lambda x: x[1], reverse=True)
        best_bot, best_cover = scored[0]

        # C1: single bot full cover.
        if best_cover >= _FULL_COVER - _EPS:
            return RouteRecommendation(
                route_class=RouteClass.C1,
                run_mode=RunMode.SINGLE_BOT,
                candidates=[BotCandidate(best_bot.bot_id, best_cover, "full cover")],
                confidence=best_cover,
                rationale="single bot fully covers sub-requirement",
            )

        # C3: multi-bot union full cover (协作群/多 bot 拼合).
        selected = _greedy_set_cover(node_kw, bots)
        if len(selected) > 1 and _union_cover(node_kw, selected) >= _FULL_COVER - _EPS:
            return RouteRecommendation(
                route_class=RouteClass.C3,
                run_mode=RunMode.COOP_GROUP,
                candidates=[
                    BotCandidate(b.bot_id, _cover(node_kw, b), "partial cover") for b in selected
                ],
                confidence=_union_cover(node_kw, selected),
                rationale="multiple bots combine to fully cover",
            )

        # C4: partial cover — needs runtime decomposition (deepresearch ①).
        if best_cover > _EPS:
            return RouteRecommendation(
                route_class=RouteClass.C4,
                run_mode=RunMode.SINGLE_BOT,
                candidates=[BotCandidate(best_bot.bot_id, best_cover, "partial cover")],
                confidence=best_cover,
                rationale="partial cover — decompose or re-search",
            )

        # C5: zero cover — escalate to BBS.
        return RouteRecommendation(
            route_class=RouteClass.C5,
            run_mode=RunMode.BBS,
            candidates=[],
            confidence=0.0,
            rationale="no executor covers any part — escalate to BBS",
        )

    def _load_node_spec(self, task_id: str, node_id: str) -> str:
        if self._task_repo is None:
            return ""
        try:
            task = self._task_repo.get_by_id(task_id)
        except Exception:
            return ""
        node = self._find_node(task, node_id)
        return node.spec if node is not None else ""

    @staticmethod
    def _find_node(task: Task, node_id: str) -> Optional[Node]:
        graph = task.execution_graph
        if graph is None:
            return None
        for n in graph.nodes:
            if n.node_id == node_id:
                return n
        return None


__all__ = ["BotDiscoverService"]