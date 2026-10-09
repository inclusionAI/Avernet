"""Unit tests for policy parsing module.

Covers:
- parse_policy normalization: fail-closed (None/empty/missing key),
  explicit allow-all ("*"), deny-all (empty / NONE sentinel / mixed),
  whitelist parsing, fail-closed on bad input.
- APIKeyPolicy: to_json round-trip, NONE/ALL sentinel constants
"""

from secbaas.community.api.api_gateway import APIKeyPolicy, parse_policy


class TestAPIKeyPolicy:
    def test_default_empty(self):
        policy = APIKeyPolicy()
        assert policy.allowed_bots == []

    def test_to_json(self):
        policy = APIKeyPolicy(allowed_bots=["bot1:entity1", "bot2:entity2"])
        result = policy.to_json()
        assert result == '{"allowed_bots":["bot1:entity1","bot2:entity2"]}'

    def test_to_json_empty(self):
        policy = APIKeyPolicy()
        result = policy.to_json()
        assert result == '{"allowed_bots":[]}'

    def test_to_json_preserves_chinese(self):
        policy = APIKeyPolicy(allowed_bots=["机器人:实体"])
        result = policy.to_json()
        assert "机器人" in result  # ensure_ascii=False

    def test_to_json_outputs_plural_key(self):
        """to_json outputs 'allowed_bots' (plural key)."""
        import json

        policy = APIKeyPolicy(allowed_bots=["b:e1"])
        result = policy.to_json()
        parsed = json.loads(result)
        assert "allowed_bots" in parsed

    def test_sentinel_constants(self):
        """NONE / ALL sentinel constants."""
        assert APIKeyPolicy.NONE == "NONE"
        assert APIKeyPolicy.ALL == "*"


class TestParsePolicy:
    def test_valid_json_with_allowed_bots(self):
        result = parse_policy('{"allowed_bots": ["bot1:e1", "bot2:e2"]}')
        assert isinstance(result, APIKeyPolicy)
        assert result.allowed_bots == ["bot1:e1", "bot2:e2"]

    # --- fail-closed: None / empty / missing key deny all ---
    def test_none_input_denies_all(self):
        result = parse_policy(None)
        assert result.allowed_bots == []

    def test_empty_string_denies_all(self):
        result = parse_policy("")
        assert result.allowed_bots == []

    def test_whitespace_string_denies_all(self):
        result = parse_policy("   ")
        assert result.allowed_bots == []

    def test_missing_allowed_bots_key_denies_all(self):
        """dict without allowed_bots key → deny all (fail-closed)."""
        result = parse_policy('{"other_key": "value"}')
        assert result.allowed_bots == []

    # --- explicit allow-all ---
    def test_explicit_allow_all(self):
        result = parse_policy('{"allowed_bots": ["*"]}')
        assert result.allowed_bots == ["*"]

    # --- deny-all: empty / NONE sentinel / fail-closed ---
    def test_empty_allowed_bots_denies_all(self):
        result = parse_policy('{"allowed_bots": []}')
        assert result.allowed_bots == []

    def test_allowed_bots_null_denies_all(self):
        result = parse_policy('{"allowed_bots": null}')
        assert result.allowed_bots == []

    def test_none_sentinel_denies_all(self):
        """Lone ['NONE'] sentinel → normalized to empty = deny all."""
        result = parse_policy('{"allowed_bots": ["NONE"]}')
        assert result.allowed_bots == []

    def test_none_sentinel_filtered_from_mixed(self):
        """['NONE', 'bot-1'] keeps ['bot-1'] (NONE filtered, not a full deny)."""
        result = parse_policy('{"allowed_bots": ["NONE", "bot-1:e1"]}')
        assert result.allowed_bots == ["bot-1:e1"]

    # --- fail-closed: bad input denies all ---
    def test_invalid_json_denies_all(self):
        result = parse_policy("not-json")
        assert result.allowed_bots == []

    def test_non_dict_json_denies_all(self):
        result = parse_policy('["list", "not", "dict"]')
        assert result.allowed_bots == []

    def test_non_list_allowed_bots_denies_all(self):
        result = parse_policy('{"allowed_bots": "bot-1"}')
        assert result.allowed_bots == []
