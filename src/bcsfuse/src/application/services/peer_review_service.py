"""PeerReviewService — 召回相似 bot 作为 peer reviewer 对被测 bot 进行面试。"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from src.domain.models.verify_dto import (
    PeerReviewItem,
    PeerReviewResult,
    PeerReviewer,
    VerifyData,
)
from src.domain.models.worker import TrustLevel, Worker

if TYPE_CHECKING:
    from src.domain.services.adapters.worker_registry_store_adapter import (
        WorkerRegistryStoreAdapter,
    )
    from src.application.services.verify_executor import VerifyExecutor
    from src.application.services.worker_candidate_recommendation_impl import (
        WorkerCandidateRecommendationImpl,
    )

logger = logging.getLogger(__name__)

_PEER_QUESTION_PROMPT = """
你是一个能力审查专家，现在需要对另一个 bot 进行专业能力审查。

你的职责不是友好交流，而是识别：该 bot 在其声明能力上，究竟是“真正具备能力”，还是“只会说空话或泛化包装”。

【重要边界】
- 你只能审查与你自己专业领域直接相关的能力
- 不属于你专业范围的能力不要出题
- 问题必须能区分真实能力与表面话术
- 不要出概念介绍题，不要出开放闲聊题

【被测 bot 自我介绍】
---
{bot_intro}
---

【被测 bot 声称具备的能力】
{capability_names}

【出题要求】
请先识别哪些声明能力属于你的专业评审范围，然后最多生成 3 个高区分度问题。

每个问题应尽量满足以下目标之一：
- 检验是否具备真实任务处理能力
- 检验是否理解关键约束、风险、失败场景
- 检验是否真正理解所声明的工具、流程或方法
- 检验是否具有边界诚实性，而不是夸大能力

[{{"question": "...", "target_capability": "..."}}]"""

_PEER_EVAL_PROMPT = """
你是一个能力审查专家。你之前已经针对另一个 bot 的相关声明能力提出了验证问题。现在请根据对方的回答进行专业评估。


【评估原则】
- 只评估属于你专业范围内的能力
- 不看表达是否自信，重点看是否专业、具体、可信
- 不因为术语多就高估，不因为措辞简洁就低估
- 重点识别：真实能力、边界诚实性、是否存在套话和夸大


【重点判断维度】
1. 是否真正回答了问题核心，而不是绕开问题
2. 是否体现出领域内的专业判断、经验模式或正确任务分解
3. 是否能说明限制条件、失败场景、风险权衡
4. 如果涉及工具/流程，是否体现出真实使用逻辑
5. 是否存在吹牛、编造、伪专业术语堆砌、回避关键约束等迹象

---
问题与回答：
{qa_pairs}
---

请基于这些回答，评判该 bot 是否真正具备声明的能力。输出 JSON：
{{"confidence": 0.0-1.0, "reasoning": "评判理由"}}

直接输出 JSON，不要其他内容。"""


class PeerReviewService:
    """召回相似 bot 并让其对被测 bot 进行 peer review 面试。

    使用 WorkerCandidateRecommendationImpl.recommend() 进行语义相似度召回，
    """

    def __init__(
        self,
        executor: VerifyExecutor,
        worker_repo: WorkerRegistryStoreAdapter,
        recommendation_service: WorkerCandidateRecommendationImpl,
        top_k: int = 2,
        min_similarity: float = 0.5,
    ) -> None:
        self._executor = executor
        self._worker_repo = worker_repo
        self._recommendation_service = recommendation_service
        self._top_k = top_k
        self._default_min_similarity = min_similarity

    def _get_min_similarity(self) -> float:
        """从 DRM 配置动态读取 peer review 相似度阈值，未配置时使用本地默认值。"""
        from src.application.utils.drm_config_helper import get_peer_min_similarity
        drm_val = get_peer_min_similarity()
        if drm_val is not None:
            return max(0.0, min(1.0, drm_val))
        return self._default_min_similarity

    async def find_peer_reviewers(
        self,
        worker: Worker,
        llm_capabilities: list[str] | None = None,
        soul_md: str = "",
    ) -> list[PeerReviewer]:
        """通过推荐服务召回相似 bot 作为 peer reviewer。

        使用 WorkerCandidateRecommendationImpl 的语义检索能力，
        以被测 bot 的能力描述作为 query 搜索相似 bot，
        然后过滤：排除自身、排除无 external_id 的、低于阈值的。
        """
        # 构建 query：优先用 LLM 能力标签，回退 worker.capabilities
        query = self._build_query(worker, llm_capabilities, soul_md)
        if not query:
            logger.info("[PeerReview] 无法构建有效 query，跳过 peer review")
            return []

        try:
            from src.domain.models.retrieval_mode import RetrievalMode

            response = self._recommendation_service.recommend(
                question=query,
                mode=RetrievalMode.EXPERT_DIAGNOSIS,
                participants=None,
                max_candidates=self._top_k * 2
            )
        except Exception:
            logger.exception("[PeerReview] 推荐服务调用失败，跳过 peer review")
            return []

        if not response.recommendations:
            logger.info("[PeerReview] 推荐服务未返回任何候选人")
            return []

        reviewers: list[PeerReviewer] = []
        for rec in response.recommendations:
            # rec.worker_id 来自 profile.staff_id（如 "default"），
            # 但 worker_repo 存储的 Worker.id 是 staff_id:profile_id 格式（如 "default:448158"）。
            # 从 profile_key（格式 staff_id:profile_id:scenario）中提取正确的 worker_id。
            lookup_id = rec.profile_key.rsplit(":", 1)[0] if rec.profile_key else rec.worker_id

            # 排除自身
            if lookup_id == worker.id:
                continue

            # 低于阈值跳过
            if rec.score < self._get_min_similarity():
                logger.info(
                    "[PeerReview] 候选 %s (profile_key=%s) score=%.3f 低于阈值 %.2f，跳过",
                    lookup_id,
                    rec.profile_key,
                    rec.score,
                    self._get_min_similarity(),
                )
                continue

            # 查找 worker 获取 BCN bot UUID
            candidate = self._worker_repo.get_by_id(lookup_id)
            if not candidate:
                logger.info(
                    "[PeerReview] 候选 %s (profile_key=%s) 在 worker_repo 中不存在，跳过",
                    lookup_id,
                    rec.profile_key,
                )
                continue

            # 非 TRUSTED 级别的 peer 不能参与 review
            if candidate.trust_level != TrustLevel.TRUSTED:
                logger.info(
                    "[PeerReview] 候选 %s trust_level=%s 非 TRUSTED，跳过",
                    lookup_id,
                    candidate.trust_level,
                )
                continue

            # BCN bot UUID: 优先 external_id，回退 worker.id（与 _verify 一致）
            bot_uuid = candidate.external_id or candidate.id

            overlap = rec.matched_skills if rec.matched_skills else [rec.domain]

            reviewers.append(PeerReviewer(
                worker_id=candidate.id,
                bot_uuid=bot_uuid,
                similarity=round(rec.score, 4),
                overlap_capabilities=overlap
            ))

        # 按相似度降序，取 top_k
        reviewers.sort(key=lambda r: r.similarity, reverse=True)
        result = reviewers[: self._top_k]

        logger.info(
            "[PeerReview] 找到 %d 个 peer reviewer (threshold=%.2f, candidates=%d, total_recs=%d)",
            len(result),
            self._get_min_similarity(),
            len(reviewers),
            len(response.recommendations),
        )
        return result

    async def conduct_peer_review(
        self,
        tested_bot_uuid: str,
        bot_intro: str,
        verify_data: VerifyData,
        peer_reviewers: list[PeerReviewer],
    ) -> list[PeerReviewResult]:
        """让 peer bot 对被测 bot 进行面试。

        流程：
        1. 让 peer bot 根据被测 bot 的自我介绍生成验证问题
        2. 将问题发送给被测 bot
        3. 让 peer bot 评判被测 bot 的回答
        """
        results: list[PeerReviewResult] = []
        cap_names = ", ".join(c.name for c in verify_data.capabilities)

        for reviewer in peer_reviewers:
            logger.info(
                "[PeerReview] 开始 peer review: peer=%s (sim=%.4f)",
                reviewer.worker_id,
                reviewer.similarity,
            )
            result = await self._review_with_peer(
                tested_bot_uuid=tested_bot_uuid,
                bot_intro=bot_intro or verify_data.soul_md,
                cap_names=cap_names,
                reviewer=reviewer,
            )
            results.append(result)

        return results

    def to_judgments(self, peer_results: list[PeerReviewResult]) -> list[tuple[str, float, str]]:
        """将 peer review 结果转换为 (capability_name, confidence, reasoning) 列表。

        用于与现有 LLM judge 结果合并。
        """
        judgments: list[tuple[str, float, str]] = []
        for pr in peer_results:
            for item in pr.items:
                judgments.append(("peer_review", item.confidence, item.peer_evaluation))
        if not judgments and peer_results:
            for pr in peer_results:
                judgments.append(("peer_review", pr.overall_confidence, pr.reasoning))
        return judgments

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_query(
        worker: Worker,
        llm_capabilities: list[str] | None = None,
        soul_md: str = "",
    ) -> str:
        """构建推荐服务的查询文本。"""
        parts: list[str] = []

        if llm_capabilities:
            parts.append("擅长能力：" + "、".join(llm_capabilities))
        elif worker.capabilities:
            cap_names = [c.name for c in worker.capabilities if c.name]
            if cap_names:
                parts.append("擅长能力：" + "、".join(cap_names))

        if worker.identity.description:
            parts.append(worker.identity.description)

        if soul_md:
            # soul_md 可能很长，截取前 500 字符
            parts.append(soul_md[:500])

        return " ".join(parts) if parts else ""

    async def _review_with_peer(
        self,
        tested_bot_uuid: str,
        bot_intro: str,
        cap_names: str,
        reviewer: PeerReviewer,
    ) -> PeerReviewResult:
        """单轮 peer review：peer 生成问题 → 被测回答 → peer 评判。"""
        # Step 1: 让 peer bot 生成验证问题
        question_prompt = _PEER_QUESTION_PROMPT.format(
            bot_intro=(bot_intro or "（无自我介绍）")[:2000],
            capability_names=cap_names,
        )
        peer_questions_resp = await self._executor.chat(reviewer.bot_uuid, question_prompt)
        questions = self._parse_questions(peer_questions_resp)

        if not questions:
            logger.warning(
                "[PeerReview] Peer %s 未能生成验证问题, raw: %s",
                reviewer.worker_id,
                peer_questions_resp[:200],
            )
            return PeerReviewResult(
                peer_worker_id=reviewer.worker_id,
                peer_bot_uuid=reviewer.bot_uuid,
                similarity=reviewer.similarity,
                items=[],
                overall_confidence=0.0,
                reasoning="Peer bot 未能生成验证问题",
            )

        logger.info(
            "[PeerReview] Peer %s 生成了 %d 个验证问题",
            reviewer.worker_id,
            len(questions),
        )

        # Step 2: 将问题发送给被测 bot
        items: list[PeerReviewItem] = []
        for q in questions:
            answer = await self._executor.chat(tested_bot_uuid, q.question)
            items.append(PeerReviewItem(
                question=q.question,
                target_capability=q.target_capability,
                tested_bot_answer=answer,
                peer_evaluation="",
                confidence=0.0,
            ))

        # Step 3: 让 peer bot 评判被测 bot 的回答
        qa_text = "\n".join(
            f"Q{i+1}: {item.question}\nA{i+1}: {item.tested_bot_answer[:500]}"
            for i, item in enumerate(items)
        )
        eval_prompt = _PEER_EVAL_PROMPT.format(qa_pairs=qa_text)
        peer_eval_resp = await self._executor.chat(reviewer.bot_uuid, eval_prompt)

        confidence, reasoning = self._parse_evaluation(peer_eval_resp)

        # 将评判结果填入 items
        for item in items:
            item.peer_evaluation = reasoning
            item.confidence = confidence

        overall = confidence if items else 0.0
        return PeerReviewResult(
            peer_worker_id=reviewer.worker_id,
            peer_bot_uuid=reviewer.bot_uuid,
            similarity=reviewer.similarity,
            items=items,
            overall_confidence=overall,
            reasoning=reasoning,
        )

    # ------------------------------------------------------------------
    # JSON parsing helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_questions(raw: str) -> list[PeerReviewItem]:
        """从 peer bot 的回复中解析验证问题。"""
        if not raw:
            return []
        try:
            text = raw.strip()
            fence_start = text.find("[")
            fence_end = text.rfind("]")
            if fence_start != -1 and fence_end != -1:
                text = text[fence_start : fence_end + 1]
            items = json.loads(text)
            if not isinstance(items, list):
                return []
            result: list[PeerReviewItem] = []
            for item in items[:5]:
                if isinstance(item, dict) and "question" in item:
                    result.append(PeerReviewItem(
                        question=str(item["question"]),
                        target_capability=str(item.get("target_capability", "")),
                    ))
            return result
        except (json.JSONDecodeError, ValueError):
            logger.warning("[PeerReview] 解析问题 JSON 失败: %s", raw[:200])
            return []

    @staticmethod
    def _parse_evaluation(raw: str) -> tuple[float, str]:
        """从 peer bot 的评判回复中解析 confidence 和 reasoning。"""
        if not raw:
            return 0.0, ""
        try:
            text = raw.strip()
            fence_start = text.find("{")
            fence_end = text.rfind("}")
            if fence_start != -1 and fence_end != -1:
                text = text[fence_start : fence_end + 1]
            data = json.loads(text)
            confidence = float(data.get("confidence", 0.0))
            confidence = max(0.0, min(1.0, confidence))
            reasoning = str(data.get("reasoning", ""))
            return confidence, reasoning
        except (json.JSONDecodeError, ValueError, TypeError):
            logger.warning("[PeerReview] 解析评判 JSON 失败: %s", raw[:200])
            return 0.0, raw[:200]