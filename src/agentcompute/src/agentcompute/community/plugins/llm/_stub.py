"""Stub LLM provider for tests/simulation — emits a fixed valid DAG JSON.

Used by tests and local demos that need a deterministic plan without a real
LLM. Not intended for run mode; run mode should use the ``openai`` provider.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from ...spi._llm import LLMProviderPlugin

__all__ = ["StubLLMProvider"]


class StubLLMProvider(LLMProviderPlugin):
    """Returns a fixed two-node DAG for planning, and a text reply otherwise."""

    def complete(self, prompt: str, **kwargs: Any) -> str:
        if kwargs.get("kind") == "planner":
            return json.dumps(
                {
                    "nodes": {
                        "1": {"agent": "searcher", "input": {"goal": "gather sources"}},
                        "2": {"agent": "summarizer", "input": {"goal": "summarize findings"}},
                    },
                    "edges": [["1", "2"]],
                },
                ensure_ascii=False,
            )
        return f"[{kwargs.get('kind', 'agent')}] stub reply"

    def stream(self, prompt: str, **kwargs: Any) -> Iterator[str]:
        reply = self.complete(prompt, **kwargs)
        yield reply
