"""The public checkout must not silently instantiate an internal vector store."""

import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("factory", ["QdrantZdasVectorStore", "get_qdrant_zdas_vector_store"])
def test_public_zdas_entrypoints_fail_closed(factory):
    # Deliberately importing the compatibility stub must not contaminate the
    # public-provider import-boundary checks in the parent pytest process.
    result = subprocess.run(
        [sys.executable, "-c", f"""
from src.infra.vectorstores.qdrant_zdas_vector_store import (
    {factory}, ZdasInternalOnlyProviderUnavailable,
)
try:
    {factory}()
except ZdasInternalOnlyProviderUnavailable as error:
    assert "internal-only" in str(error)
else:
    raise AssertionError("public internal-provider entrypoint did not fail closed")
"""],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
