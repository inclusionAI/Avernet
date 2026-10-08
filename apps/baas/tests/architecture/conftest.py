"""Shared fixtures for architecture enforcement tests."""

from pathlib import Path

import pytest
from pytestarch import EvaluableArchitecture, get_evaluable_architecture


@pytest.fixture(scope="session")
def project_architecture() -> EvaluableArchitecture:
    """Build the evaluable architecture graph once per test session."""
    src_dir = str(Path(__file__).resolve().parents[2] / "src" / "secbaas")
    return get_evaluable_architecture(
        src_dir,
        src_dir,
        ("*__pycache__",),
    )
