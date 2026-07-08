"""Unit tests for policy parsing module.

Covers:
- parse_policy: valid JSON, None input, empty string, invalid JSON, non-dict JSON
- APIKeyPolicy: to_json round-trip, NONE sentinel
- parse_policy: NONE sentinel policy
"""

from secbaas.api.api_gateway import APIKeyPolicy, parse_policy


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

    def test_none_sentinel_constant(self):
        """NONE sentinel constant should be 'NONE'."""
        assert APIKeyPolicy.NONE == "NONE"

    def test_to_json_with_none_sentinel(self):
        """Policy with NONE sentinel serializes correctly."""
        policy = APIKeyPolicy(allowed_bots=["NONE"])
        result = policy.to_json()
        assert result == '{"allowed_bots":["NONE"]}'


class TestParsePolicy:
    def test_valid_json_with_allowed_bots(self):
        result = parse_policy('{"allowed_bots": ["bot1:e1", "bot2:e2"]}')
        assert isinstance(result, APIKeyPolicy)
        assert result.allowed_bots == ["bot1:e1", "bot2:e2"]

    def test_valid_json_without_allowed_bots(self):
        result = parse_policy('{"other_key": "value"}')
        assert isinstance(result, APIKeyPolicy)
        assert result.allowed_bots == []

    def test_none_input(self):
        result = parse_policy(None)
        assert isinstance(result, APIKeyPolicy)
        assert result.allowed_bots == []

    def test_empty_string(self):
        result = parse_policy("")
        assert isinstance(result, APIKeyPolicy)
        assert result.allowed_bots == []

    def test_whitespace_string(self):
        result = parse_policy("   ")
        assert isinstance(result, APIKeyPolicy)
        assert result.allowed_bots == []

    def test_invalid_json(self):
        result = parse_policy("not-json")
        assert isinstance(result, APIKeyPolicy)
        assert result.allowed_bots == []

    def test_non_dict_json(self):
        result = parse_policy('["list", "not", "dict"]')
        assert isinstance(result, APIKeyPolicy)
        assert result.allowed_bots == []

    def test_allowed_bots_null(self):
        result = parse_policy('{"allowed_bots": null}')
        assert isinstance(result, APIKeyPolicy)
        assert result.allowed_bots == []

    def test_parse_none_sentinel_policy(self):
        """Parse a policy that contains the NONE sentinel."""
        result = parse_policy('{"allowed_bots": ["NONE"]}')
        assert result.allowed_bots == ["NONE"]
