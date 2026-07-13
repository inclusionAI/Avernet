"""
HTTP Reranker 实现

支持模型：
- bge-reranker-v2-m3: BGE 官方格式（简洁，高区分度）
- Qwen3-Reranker-4B: Chat 格式（带 prompt 模板）
"""

from __future__ import annotations

import logging
import os
import time
import uuid

import requests

from src.domain.services.reranker import Reranker, RerankResult

logger = logging.getLogger(__name__)


class HttpReranker(Reranker):
    """
    HTTP API Reranker - 支持 bge-reranker-v2-m3 和 Qwen3-Reranker-4B

    环境变量配置：
    - RERANKER_BASE_URL: API 基础 URL (required)
    - RERANKER_API_KEY: API 密钥
    - RERANKER_MODEL: 模型名称 (默认: bge-reranker-v2-m3)

    支持模型：
    - bge-reranker-v2-m3: 使用 BGE 官方格式
    - Qwen3-Reranker-4B: 使用 Chat Prompt 格式
    """

    # Qwen3-Reranker-4B: Chat 格式模板
    CHAT_PREFIX = "<|im_start|>system\nJudge whether the Document meets the requirements based on the Query and the Instruct provided. Note that the answer can only be yes or no.。\n<|im_start|>user\n"
    CHAT_SUFFIX = "。\n<|im_start|>assistant\n得分\n\n。\n"
    CHAT_INSTRUCTION = "根据用户问题，找出最相关的文档。"

    # 支持的模型列表
    SUPPORTED_MODELS = ["bge-reranker-v2-m3", "Qwen3-Reranker-4B"]

    # 默认模型
    DEFAULT_MODEL = "bge-reranker-v2-m3"

    def __init__(self):
        self.base_url = os.getenv("RERANKER_BASE_URL", os.getenv(
            "EMBEDDING_BASE_URL", ""))
        self.api_key = os.getenv("RERANKER_API_KEY", os.getenv(
            "EMBEDDING_AUTH_TOKEN", ""))
        self.model = os.getenv("RERANKER_MODEL", self.DEFAULT_MODEL)
        self.timeout = int(os.getenv("RERANKER_TIMEOUT", "30"))

        # 模型名称归一化（支持大小写不敏感）
        model_lower = self.model.lower()
        if "bge" in model_lower and "rerank" in model_lower:
            self.model = "bge-reranker-v2-m3"
            self._use_bge_format = True
        elif "qwen" in model_lower and "rerank" in model_lower:
            self.model = "Qwen3-Reranker-4B"
            self._use_bge_format = False
        else:
            # 默认使用 BGE 格式（更简洁高效）
            self._use_bge_format = True
            if self.model not in self.SUPPORTED_MODELS:
                logger.warning(f"Unknown model '{self.model}', using BGE format by default")

        logger.debug("[HttpReranker] Model: %s, Format: %s", self.model, 'BGE' if self._use_bge_format else 'Chat')

    def _format(self, query: str, documents: list[str]) -> tuple[str, list[str]]:
        """
        根据模型类型选择格式化方式

        bge-reranker-v2-m3: BGE 官方格式
          - query: "question\n"
          - documents: ["Passage: doc1", "Passage: doc2", ...]

        Qwen3-Reranker-4B: Chat Prompt 格式
          - query: <|im_start|>system...<Instruct>...<Query>...<Document>:
          - documents: ["doc1<suffix>", "doc2<suffix>", ...]
        """
        if self._use_bge_format:
            # BGE 官方格式（简洁，高区分度）
            formatted_query = query + "\n"
            formatted_docs = [f"Passage: {doc}" for doc in documents]
            return formatted_query, formatted_docs
        else:
            # Qwen3 Chat 格式（带完整 prompt 模板）
            formatted_query = (
                self.CHAT_PREFIX +
                f"<Instruct>: {self.CHAT_INSTRUCTION}\n" +
                f"<Query>: {query}\n" +
                "<Document>: "
            )
            formatted_docs = [doc + self.CHAT_SUFFIX for doc in documents]
            return formatted_query, formatted_docs

    def rerank(self, query: str, candidates: list[dict], top_k: int = 5) -> list[RerankResult]:
        """
        执行重排序

        Args:
            query: 查询文本
            candidates: 候选列表，每个元素为 {"id": str, "text": str}
            top_k: 返回数量

        Returns:
            按分数降序排列的 RerankResult 列表
        """
        if not candidates:
            return []

        if not self.api_key:
            logger.warning("No API key configured, returning original order")
            return [RerankResult(candidate_id=c["id"], score=i/len(candidates))
                    for i, c in enumerate(candidates)]

        # 格式化输入
        raw_docs = [c.get("text", "") for c in candidates]
        formatted_query, formatted_docs = self._format(query, raw_docs)

        # 调用 API
        try:
            scores = self._call_api(formatted_query, formatted_docs)

            # 构建结果
            results = []
            for i, candidate in enumerate(candidates):
                score = scores[i] if i < len(scores) else 0.0
                results.append(RerankResult(
                    candidate_id=candidate.get("id", str(i)),
                    score=score,
                    metadata={"text": candidate.get("text", "")[:100]}
                ))

            # 按分数降序排序
            results.sort(key=lambda x: x.score, reverse=True)
            return results[:top_k]

        except Exception as e:
            logger.error(f"Rerank failed: {e}")
            # 失败时返回原始顺序
            return [RerankResult(candidate_id=c["id"], score=0.0)
                    for c in candidates[:top_k]]

    def _call_api(self, query: str, documents: list[str]) -> list[float]:
        """调用 Reranker API"""
        url = f"{self.base_url}/rerank"
        request_id = f"rerank-{uuid.uuid4().hex[:8]}"

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
            "X-Request-Id": request_id,
        }

        payload = {
            "model": self.model,
            "query": query,
            "documents": documents,
        }

        response = requests.post(url, headers=headers, json=payload, timeout=self.timeout)
        response.raise_for_status()

        result = response.json()
        return self._parse_response(result, len(documents))

    def _parse_response(self, result: dict, num_docs: int) -> list[float]:
        """解析 API 响应，支持多种格式"""
        scores = []

        if "results" in result:
            # 格式: {"results": [{"index": 0, "relevance_score": 0.9}, ...]}
            sorted_results = sorted(result["results"], key=lambda x: x.get("index", 0))
            scores = [r.get("relevance_score", 0.0) for r in sorted_results]
            # DIAGNOSTIC: Log parsed relevance scores
            logger.info(
                "[RERANKER-HTTP] Parsed 'results' format | count=%d | "
                "top_scores=%s | all_scores=%s",
                len(scores),
                scores[:3] if scores else [],
                scores
            )
        elif "scores" in result:
            scores = result["scores"]
            logger.info(
                "[RERANKER-HTTP] Parsed 'scores' format | count=%d | top_scores=%s",
                len(scores),
                scores[:3] if scores else []
            )
        elif "data" in result and "scores" in result["data"]:
            scores = result["data"]["scores"]
            logger.info(
                "[RERANKER-HTTP] Parsed 'data.scores' format | count=%d | top_scores=%s",
                len(scores),
                scores[:3] if scores else []
            )
        elif "output" in result:
            scores = result["output"].get("scores", [])
            logger.info(
                "[RERANKER-HTTP] Parsed 'output' format | count=%d | top_scores=%s",
                len(scores),
                scores[:3] if scores else []
            )
        else:
            logger.warning(f"Unknown response format: {list(result.keys())}")
            scores = [0.0] * num_docs

        # 确保长度匹配
        while len(scores) < num_docs:
            scores.append(0.0)

        logger.info(
            "[RERANKER-HTTP] Final parsed scores | count=%d | min=%.4f | max=%.4f | scores=%s",
            len(scores),
            min(scores) if scores else 0.0,
            max(scores) if scores else 0.0,
            scores
        )

        return scores[:num_docs]


def get_reranker() -> Reranker:
    """获取默认 Reranker 实例"""
    return HttpReranker()
