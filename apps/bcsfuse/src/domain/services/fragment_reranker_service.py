"""
Fragment Reranker Service

对 Fragment 聚合后的候选 Profile 进行精排。
"""

from __future__ import annotations

import logging
import math
import re
from time import perf_counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextvars import copy_context
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol

from src.domain.models.profile_fragment import FragmentMatch
from src.domain.services.retrieval_logging import log_stage, log_candidates

logger = logging.getLogger(__name__)


# Reranker 抽象（避免循环导入）
class RerankerProtocol(Protocol):
    """Reranker 协议"""

    def rerank(self, query: str, candidates: list[dict], top_k: int = 5) -> list:
        """执行重排序"""
        ...


class RerankFailAction(Enum):
    """精排失败时的处理策略"""
    DEGRADE = "degrade"  # 退化到加权聚合
    EMPTY = "empty"      # 返回空结果


@dataclass
class ProfileCandidate:
    """候选 Profile（聚合后的结果）"""
    profile_key: str
    aggregated_score: float  # 聚合分数（V1 策略分数）
    fragments: list[FragmentMatch]  # 匹配的 fragments
    metadata: dict[str, Any] = field(default_factory=dict)  # 完整的 profile 元数据


@dataclass
class RerankRequest:
    """精排请求"""
    query: str
    candidates: list[ProfileCandidate]
    top_k: int = 10


@dataclass
class RerankResult:
    """精排结果"""
    profile_key: str
    final_score: float  # 精排后的分数
    original_score: float  # 原始聚合分数
    rank: int
    rerank_metadata: dict[str, Any] = field(default_factory=dict)


class FragmentRerankerService:
    """
    Fragment 检索精排服务

    支持：
    - Reranker 精排（自动根据 model 创建对应 reranker）
    - 失败退化机制
    """

    def __init__(
        self,
        reranker_model: str | None = None,
        reranker: RerankerProtocol | None = None,
        fail_action: RerankFailAction = RerankFailAction.DEGRADE,
    ):
        """
        初始化精排服务

        Args:
            reranker_model: Reranker 模型名称（如 bge-reranker-v2-m3）
            reranker: 可选的直接传入 reranker 实例
            fail_action: 失败时的处理策略
        """
        self._fail_action = fail_action
        self._reranker_model = reranker_model

        # 优先使用传入的 reranker 实例，否则根据 model 创建
        if reranker is not None:
            self._reranker = reranker
            logger.debug("[FragmentReranker] Using provided reranker instance")
        elif reranker_model:
            try:
                from src.infra.reranker import get_reranker
                self._reranker = get_reranker(model=reranker_model)
                logger.debug("[FragmentReranker] Created reranker with model: %s", reranker_model)
            except Exception as e:
                logger.error(f"[FragmentReranker] Failed to create reranker: {e}")
                self._reranker = None
        else:
            self._reranker = None

        if self._reranker is None and fail_action != RerankFailAction.DEGRADE:
            raise ValueError("Reranker creation failed and fail_action is not DEGRADE")

    @property
    def model(self) -> str | None:
        """Report the adapter's resolved model, including canonicalized aliases."""
        if self._reranker is None:
            return None
        return getattr(self._reranker, "model", self._reranker_model)

    # 32K token 上下文限制估算（留足余量）
    MAX_CONTEXT_CHARS = 24000  # 约 6K tokens，20K给system，6K给candidates

    # Reranker 模型 batch size 限制
    MAX_BATCH_SIZE = 32  # 每批最多32个候选

    def rerank(self, request: RerankRequest) -> list[RerankResult]:
        """
        执行精排

        流程：
        1. 准备 candidate texts
        2. 判断是否需要分批（超过 32K token 限制或batch size限制）
        3. 调用 Reranker API（分批或单批）
        4. 按精排分数排序
        5. 失败时根据策略处理

        Args:
            request: 精排请求

        Returns:
            精排后的结果列表
        """
        if not request.candidates:
            return []

        # 如果 reranker 不可用，直接降级
        if self._reranker is None:
            logger.warning("[FragmentReranker] Reranker not available, degrading")
            return self._handle_failure(request)

        started = perf_counter()
        try:
            # 准备 reranker 输入
            reranker_candidates = self._prepare_candidates(request.candidates)

            # 计算总字符数，判断是否需要分批
            total_chars = sum(len(c["text"]) for c in reranker_candidates)
            query_len = len(request.query)
            num_candidates = len(reranker_candidates)

            # 如果超过限制，使用分批 rerank
            if total_chars + query_len > self.MAX_CONTEXT_CHARS or num_candidates > self.MAX_BATCH_SIZE:
                logger.debug(
                    "[FragmentReranker] Using batch rerank: chars=%d (limit=%d), candidates=%d (limit=%d)",
                    total_chars + query_len, self.MAX_CONTEXT_CHARS,
                    num_candidates, self.MAX_BATCH_SIZE
                )
                results = self._batch_rerank(request.query, reranker_candidates, request.candidates, request.top_k)
            else:
                results = self._single_rerank(request.query, reranker_candidates, request.candidates, request.top_k)

            log_stage(logger, "reranker", input_count=num_candidates, output_count=len(results),
                      reranker_model=self.model, input_chars=total_chars,
                      duration_ms=round((perf_counter() - started) * 1000, 2),
                      degraded_count=sum(bool(r.rerank_metadata.get("degraded")) for r in results))
            log_candidates(logger, "reranker_output", ((r.profile_key, r.final_score) for r in results))
            return results

        except Exception as e:
            logger.error("[FragmentReranker] failed: %s", e)
            # 退化时使用原始聚合分数
            return self._handle_failure_with_original_scores(request)

    def _single_rerank(
        self,
        query: str,
        reranker_candidates: list[dict],
        original_candidates: list[ProfileCandidate],
        top_k: int
    ) -> list[RerankResult]:
        """单批次 rerank"""
        logger.debug(
            "[FragmentReranker] single batch | query_len=%d, candidates=%d, top_k=%d",
            len(query), len(reranker_candidates), top_k
        )

        reranker_results = self._reranker.rerank(
            query=query,
            candidates=reranker_candidates,
            top_k=top_k,
        )

        results = self._convert_results(reranker_results, original_candidates)
        if len(results) != min(top_k, len(original_candidates)) or len({r.profile_key for r in results}) != len(results):
            raise ValueError("incomplete reranker response")
        if any(isinstance(r.final_score, bool) or not math.isfinite(r.final_score) for r in results):
            raise ValueError("non-finite reranker score")

        logger.debug(
            "[FragmentReranker] single batch done | input=%d, output=%d",
            len(original_candidates), len(results)
        )

        return results

    def _batch_rerank(
        self,
        query: str,
        reranker_candidates: list[dict],
        original_candidates: list[ProfileCandidate],
        top_k: int
    ) -> list[RerankResult]:
        """
        分批 rerank（并行执行）

        将超过上下文限制或batch size限制的 candidates 分成多个批次，**并行** rerank 后合并结果。
        """
        query_len = len(query)

        # 按批次大小分组（预留 query 长度，同时考虑batch size限制）
        batches = []
        current_batch = []
        current_batch_chars = query_len

        for cand in reranker_candidates:
            cand_len = len(cand["text"])

            # 如果当前批次加上这个 candidate 会超过限制，开启新批次
            # 限制条件：1. 字符数超限 或 2. batch size超限
            if current_batch and (
                current_batch_chars + cand_len > self.MAX_CONTEXT_CHARS
                or len(current_batch) >= self.MAX_BATCH_SIZE
            ):
                batches.append(current_batch)
                current_batch = [cand]
                current_batch_chars = query_len + cand_len
            else:
                current_batch.append(cand)
                current_batch_chars += cand_len

        # 添加最后一个批次
        if current_batch:
            batches.append(current_batch)

        logger.debug(
            "[FragmentReranker] split into %d batches (max_batch_size=%d), parallel execution",
            len(batches), self.MAX_BATCH_SIZE
        )

        # 并行执行所有批次的 rerank
        all_results = []

        def _rerank_one_batch(batch_info: tuple[int, list[dict]]) -> tuple[int, list[dict]]:
            """执行单个批次的 rerank，返回 (batch_idx, results_list)"""
            batch_idx, batch = batch_info
            batch_results = []

            try:
                results = self._reranker.rerank(
                    query=query,
                    candidates=batch,
                    top_k=len(batch),  # 每批取全部，最后统一排序
                )

                # 转换结果
                for rr in results:
                    if hasattr(rr, 'candidate_id'):
                        candidate_id = rr.candidate_id
                        score = rr.score
                    elif isinstance(rr, dict):
                        candidate_id = rr.get("candidate_id") or rr.get("id")
                        score = rr["score"]
                    else:
                        continue

                    batch_results.append({
                        "profile_key": candidate_id,
                        "score": score,
                        "batch": batch_idx,
                    })

            except Exception as e:
                logger.error(f"[FragmentReranker] Batch {batch_idx} failed: {e}")
                # 失败的批次使用原始分数标记
                for cand in batch:
                    batch_results.append({
                        "profile_key": cand["id"],
                        "score": 0.0,
                        "batch": batch_idx,
                        "failed": True,
                    })

            if (len(batch_results) != len(batch)
                    or {r["profile_key"] for r in batch_results} != {c["id"] for c in batch}
                    or any(isinstance(r["score"], bool) or not math.isfinite(r["score"]) for r in batch_results)):
                raise ValueError("incomplete or invalid reranker batch")
            return batch_idx, batch_results

        # 使用 ThreadPoolExecutor 并行执行所有批次
        # max_workers 根据批次数量动态调整，但最多开 5 个线程（避免对下游造成过大压力）
        max_workers = min(len(batches), 5)

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # 提交所有任务
            future_to_idx = {
                executor.submit(copy_context().run, _rerank_one_batch, (i, batch)): i
                for i, batch in enumerate(batches)
            }

            # 收集结果（使用 as_completed 可以尽快处理已完成的批次）
            for future in as_completed(future_to_idx):
                batch_idx, batch_results = future.result()
                all_results.extend(batch_results)
                logger.debug(
                    "[FragmentReranker] batch %d/%d done: %d results",
                    batch_idx + 1, len(batches), len(batch_results)
                )

        # 按分数排序，取 top_k
        if any(result.get("failed") for result in all_results):
            raise RuntimeError("reranker batch failed; discard all partial scores")
        all_results.sort(key=lambda x: (-x["score"], x["profile_key"]))
        top_results = all_results[:top_k]

        # 构建最终 RerankResult 列表
        candidate_map = {c.profile_key: c for c in original_candidates}
        final_results = []

        for rank, result in enumerate(top_results, 1):
            profile_key = result["profile_key"]
            score = result["score"]
            original = candidate_map.get(profile_key)

            if original:
                # A failed batch has already triggered whole-request fallback.
                final_score = score

                final_results.append(RerankResult(
                    profile_key=profile_key,
                    final_score=final_score,
                    original_score=original.aggregated_score,
                    rank=rank,
                    rerank_metadata={
                        "reranker_model": self._reranker_model or "unknown",
                        "fragment_count": len(original.fragments),
                        "batch": result.get("batch", 0),
                        "degraded": False,
                    },
                ))

        logger.debug(
            "[FragmentReranker] batch rerank done | total=%d, returned=%d, workers=%d",
            len(all_results), len(final_results), max_workers
        )

        return final_results

    def _prepare_candidates(
        self,
        candidates: list[ProfileCandidate],
    ) -> list[dict]:
        """Keep identity in the candidate ID; rank only name and profile content."""
        return [
            {"id": cand.profile_key, "text": self._extract_description(cand)}
            for cand in candidates
        ]

    # 32K token 上下文限制，按字符估算（1 token ≈ 4 chars）
    MAX_RERANK_TEXT_CHARS = 8000  # 约 2K tokens，留足余量给系统和 query

    def _extract_description(self, candidate: ProfileCandidate) -> str:
        """Use profile fragments only; never substitute default skills or full."""
        profile = candidate.metadata.get("profile", "")
        if not profile:
            profile = "\n".join(
                frag.content or frag.content_preview
                for frag in candidate.fragments if frag.fragment_type == "profile"
            )
        lines = []
        for line in profile.splitlines():
            line = line.strip()
            if not line or line.startswith("worker_id:") or re.fullmatch(r"【[^】]+】", line):
                continue
            # Strip known empty fields, not meaningful statements about failures.
            value = re.split(r"[:：]", line, maxsplit=1)[-1].strip().rstrip("。.")
            if value.lower() in {"", "无", "无配置", "未配置", "未提供", "暂无", "none", "null", "n/a"}:
                continue
            if re.fullmatch(r"无配置[，,]\s*无法提供(?:身份|目标|能力|技能)", value):
                continue
            if line not in lines:
                lines.append(line)
        name = (candidate.metadata.get("display_name") or "").strip()
        if name and not any(name in line for line in lines):
            lines.insert(0, f"名称: {name}")
        return "\n".join(lines)[:self.MAX_RERANK_TEXT_CHARS]

    def _convert_results(
        self,
        reranker_results: list,
        original_candidates: list[ProfileCandidate],
    ) -> list[RerankResult]:
        """将 Reranker 结果转换为 RerankResult"""
        # 构建原始 candidate 查找表
        candidate_map = {c.profile_key: c for c in original_candidates}

        # Zero is a valid model score, not an implicit failure signal.
        all_scores_zero = all(
            (getattr(rr, 'score', 0.0) if hasattr(rr, 'score') else rr.get('score', 0.0)) == 0.0
            for rr in reranker_results
        )
        # DIAGNOSTIC: Log incoming reranker results
        logger.debug(
            "[RERANKER-CONVERT] Incoming reranker_results count=%d | all_scores_zero=%s",
            len(reranker_results),
            all_scores_zero
        )
        for i, rr in enumerate(reranker_results[:3]):  # Log top 3
            if hasattr(rr, 'candidate_id'):
                cid = rr.candidate_id
                score = rr.score
            elif isinstance(rr, dict):
                cid = rr.get("candidate_id") or rr.get("id")
                score = rr["score"]
            else:
                continue
            logger.debug(
                "[RERANKER-CONVERT] reranker_result[%d] | candidate_id=%s | reranker_score=%.4f",
                i, cid, score
            )

        results = []
        # reranker_results 已经是按分数降序排列的
        for rank, rr in enumerate(reranker_results, 1):
            # 处理不同格式的 reranker 结果
            if hasattr(rr, 'candidate_id'):
                candidate_id = rr.candidate_id
                score = rr.score
            elif isinstance(rr, dict):
                candidate_id = rr.get("candidate_id") or rr.get("id")
                score = rr["score"]
            else:
                logger.warning(f"Unknown reranker result format: {type(rr)}")
                continue

            original = candidate_map.get(candidate_id)
            if original:
                # Preserve the model score, including zero.
                final_score = score

                # DIAGNOSTIC: Log conversion for top 3
                if rank <= 3:
                    logger.debug(
                        "[RERANKER-CONVERT] result[%d] | profile_key=%s | reranker_score=%.4f | "
                        "original_score=%.4f | final_score=%.4f",
                        rank, candidate_id, score, original.aggregated_score, final_score
                    )

                results.append(RerankResult(
                    profile_key=candidate_id,
                    final_score=final_score,
                    original_score=original.aggregated_score,
                    rank=rank,
                    rerank_metadata={
                        "reranker_model": self.model or "unknown",
                        "fragment_count": len(original.fragments),
                        "degraded": False,
                    },
                ))

        return results

    def _handle_failure_with_original_scores(self, request: RerankRequest) -> list[RerankResult]:
        """
        处理精排失败

        根据 fail_action 策略决定如何降级。
        """
        if self._fail_action == RerankFailAction.EMPTY:
            logger.warning(
                "[FragmentReranker] Returning empty due to failure: "
                "candidates=%d",
                len(request.candidates)
            )
            return []

        # DEGRADE: 退化为按聚合分数排序
        logger.warning(
            "[FragmentReranker] Degrading to aggregated score sorting: "
            "input=%d, top_k=%d",
            len(request.candidates),
            request.top_k
        )

        # 按原始聚合分数排序
        sorted_candidates = sorted(
            request.candidates,
            key=lambda x: x.aggregated_score,
            reverse=True,
        )[:request.top_k]

        return [
            RerankResult(
                profile_key=c.profile_key,
                final_score=c.aggregated_score,  # 使用原始分数
                original_score=c.aggregated_score,
                rank=i + 1,
                rerank_metadata={"degraded": True, "reason": "rerank_failed"},
            )
            for i, c in enumerate(sorted_candidates)
        ]


__all__ = [
    "FragmentRerankerService",
    "RerankRequest",
    "RerankResult",
    "ProfileCandidate",
    "RerankFailAction",
]
