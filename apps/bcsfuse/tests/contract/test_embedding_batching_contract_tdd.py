"""The deployment, composition, and implementation query-batching values agree."""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import yaml


_ROOT = Path(__file__).resolve().parents[2]
_CONFIG_PATH = _ROOT / "configs/application.yaml"
_CONTRACT_PATH = _ROOT / "docs/architecture/composition-contract.md"


def test_query_batch_wait_contract_matches_deployment_and_impl() -> None:
    config = yaml.safe_load(_CONFIG_PATH.read_text()) or {}
    wait = config["embedding"]["cache"]["query_batch_wait_seconds"]

    from app.infra.public.embedding.cached_embedding_provider import (
        CachedEmbeddingProvider,
    )

    assert wait == 0.05
    signature = inspect.signature(CachedEmbeddingProvider)
    assert signature.parameters["query_batch_wait_seconds"].default == 0.05

    contract_text = _CONTRACT_PATH.read_text()
    match = re.search(
        r"query_batch_wait_seconds` \(default ([0-9.]+)\)",
        contract_text,
    )
    assert match is not None
    assert float(match.group(1)) == 0.05
