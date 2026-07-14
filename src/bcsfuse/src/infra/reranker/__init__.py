"""
Reranker 模块

支持的模型：
- bge-reranker-v2-m3: BGE 官方格式
- Qwen3-Reranker-4B: Chat Prompt 格式

用法：
    from src.infra.reranker import get_reranker

    reranker = get_reranker()
    results = reranker.rerank(
        query="用户问题",
        candidates=[{"id": "doc1", "text": "文档内容"}],
        top_k=5
    )
"""

from src.infra.reranker.http_reranker import HttpReranker, get_reranker

__all__ = ["HttpReranker", "get_reranker"]
