import os
from unittest.mock import patch


def test_default_engine_type_is_hardcoded_openclaw():
    """DEFAULT_ENGINE_TYPE 硬编码为 openclaw，不受 CHAT_ENGINE 环境变量影响。"""
    from agentclaw.community.core.workspace.constants import DEFAULT_ENGINE_TYPE
    assert DEFAULT_ENGINE_TYPE == "openclaw"


def test_supported_engine_types():
    from agentclaw.community.core.workspace.constants import SUPPORTED_ENGINE_TYPES
    assert "openclaw" in SUPPORTED_ENGINE_TYPES
    assert "moltis" in SUPPORTED_ENGINE_TYPES
    assert "aicoding" in SUPPORTED_ENGINE_TYPES
    assert "hermes" in SUPPORTED_ENGINE_TYPES


def test_get_engine_types_returns_default_when_no_env():
    from agentclaw.community.core.workspace.constants import _get_engine_types, SUPPORTED_ENGINE_TYPES
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("ENGINE_TYPES", None)
        assert _get_engine_types() == SUPPORTED_ENGINE_TYPES


def test_get_engine_types_reads_env_var():
    from agentclaw.community.core.workspace.constants import _get_engine_types
    with patch.dict(os.environ, {"ENGINE_TYPES": "openclaw,custom"}):
        assert _get_engine_types() == ["openclaw", "custom"]


def test_get_engine_types_trailing_comma():
    from agentclaw.community.core.workspace.constants import _get_engine_types
    with patch.dict(os.environ, {"ENGINE_TYPES": "openclaw,"}):
        assert _get_engine_types() == ["openclaw"]


def test_get_engine_types_whitespace_entries():
    from agentclaw.community.core.workspace.constants import _get_engine_types
    with patch.dict(os.environ, {"ENGINE_TYPES": " , moltis , openclaw , "}):
        assert _get_engine_types() == ["moltis", "openclaw"]