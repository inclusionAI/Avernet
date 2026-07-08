"""Unit tests for resolve_bot_id_from_api_key and _normalize_bot_id helpers."""

from datetime import datetime

import pytest

from secbaas.adapters.web.routers.open_api.dependencies import (
    _normalize_bot_id,
    resolve_bot_id_from_api_key,
)
from secbaas.api.api_gateway import APIKeyRecord

# ── helper ────────────────────────────────────────────────────


def _make_api_key_record(**overrides) -> APIKeyRecord:
    defaults = {
        "id": 1,
        "gmt_create": datetime.now(),
        "gmt_modified": datetime.now(),
        "api_key_hash": "fake_hash",
        "api_key_prefix": "kp-001",
        "key_name": "test-key",
        "app_id": "bot-1:entity-1",
        "app_type": "system",
        "description": None,
        "rate_limit_rpm": None,
        "rate_limit_rpd": None,
        "status": "ACTIVE",
        "owner": "owner-1",
        "tenant": "tenant-1",
        "env": "test",
        "creator": "creator-1",
        "modifier": None,
        "policy": None,
    }
    defaults.update(overrides)
    return APIKeyRecord(**defaults)


# ── resolve_bot_id_from_api_key ───────────────────────────────


class TestResolveBotIdFromApiKey:
    def test_simple_app_id_no_entity_suffix(self):
        """Returns app_id unchanged when it has no entity_id suffix."""
        record = _make_api_key_record(app_id="simple_bot")
        result = resolve_bot_id_from_api_key(record)
        assert result == "simple_bot"

    def test_strips_leading_zeros_from_entity_id(self):
        """Strips leading zeros: 'bot:000123' → 'bot:123'."""
        record = _make_api_key_record(app_id="bot:000123")
        result = resolve_bot_id_from_api_key(record)
        assert result == "bot:123"

    def test_already_normalized_unchanged(self):
        """Returns app_id unchanged when already normalized."""
        record = _make_api_key_record(app_id="bot:123")
        result = resolve_bot_id_from_api_key(record)
        assert result == "bot:123"

    def test_entity_id_zero_preserved(self):
        """'bot:0' stays 'bot:0' — stripping leading zeros from '0' gives '0'."""
        record = _make_api_key_record(app_id="bot:0")
        result = resolve_bot_id_from_api_key(record)
        assert result == "bot:0"

    def test_entity_id_all_zeros(self):
        """'bot:000' normalises to 'bot:0'."""
        record = _make_api_key_record(app_id="bot:000")
        result = resolve_bot_id_from_api_key(record)
        assert result == "bot:0"

    def test_multi_segment_app_id_with_leading_zeros(self):
        """app_id like 'real_bot:000123' normalises to 'real_bot:123'."""
        record = _make_api_key_record(app_id="real_bot:000123")
        result = resolve_bot_id_from_api_key(record)
        assert result == "real_bot:123"


# ── _normalize_bot_id ────────────────────────────────────────


class TestNormalizeBotId:
    def test_normalizes_leading_zeros(self):
        """'real_bot_id:000123' → 'real_bot_id:123'."""
        result = _normalize_bot_id("real_bot_id:000123")
        assert result == "real_bot_id:123"

    def test_no_colon_passthrough(self):
        """'simple_id' without colon passes through unchanged."""
        result = _normalize_bot_id("simple_id")
        assert result == "simple_id"

    def test_zero_entity_id_preserved(self):
        """'bot:0' → 'bot:0' (stripping leading zeros from '0' gives '0')."""
        result = _normalize_bot_id("bot:0")
        assert result == "bot:0"

    def test_strips_leading_zeros_from_entity(self):
        """'bot:007' → 'bot:7'."""
        result = _normalize_bot_id("bot:007")
        assert result == "bot:7"

    def test_all_zeros_entity(self):
        """'bot:000' → 'bot:0'."""
        result = _normalize_bot_id("bot:000")
        assert result == "bot:0"

    def test_already_normalized(self):
        """Already normalised value stays the same."""
        result = _normalize_bot_id("bot:42")
        assert result == "bot:42"

    def test_empty_string_no_colon(self):
        """Empty string has no colon → passes through."""
        result = _normalize_bot_id("")
        assert result == ""

    def test_colon_with_empty_entity(self):
        """'bot:' has empty entity_id after colon → stripping gives '0'."""
        result = _normalize_bot_id("bot:")
        assert result == "bot:0"
