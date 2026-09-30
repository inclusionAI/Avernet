"""
Retrievers Module

M5: Unified Retrieval Fabric

提供 Retriever 的具体实现。
"""

from src.infra.retrievers.baseline_retriever import BaselineRetriever, CandidateCatalog

__all__ = [
    "BaselineRetriever",
    "CandidateCatalog",
]