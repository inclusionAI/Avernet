import pytest

from src.interfaces.api.schemas.fusion_schemas import FusionRequest


def test_accepts_session_id() -> None:
    req = FusionRequest(question="q", participants=["a"], session_id="sess-1")
    assert req.session_id == "sess-1"


def test_session_id_defaults_none() -> None:
    req = FusionRequest(question="q", participants=["a"])
    assert req.session_id is None


def test_unknown_fields_still_forbidden() -> None:
    with pytest.raises(Exception):
        FusionRequest(question="q", participants=["a"], surprise="x")
