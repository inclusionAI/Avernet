"""Unit tests for encryptable header rule models."""

from secbaas.api.device_manage import (
    EncryptableHeaderRule,
    EncryptableOutBoundRule,
)


class TestEncryptableHeaderRule:
    """Test EncryptableHeaderRule model."""

    def test_default_encrypt_value_false(self):
        rule = EncryptableHeaderRule(
            domains=["*.example.com"],
            action="SET_HEADER",
            header_name="Authorization",
            value="Bearer token",
        )
        assert rule.encrypt_value is False

    def test_encrypt_value_true(self):
        rule = EncryptableHeaderRule(
            domains=["*.secure.com"],
            action="SET_HEADER",
            header_name="X-API-Key",
            value="secret-123",
            encrypt_value=True,
        )
        assert rule.encrypt_value is True

    def test_inherits_header_fields(self):
        rule = EncryptableHeaderRule(
            domains=["*.api.com"],
            action="SET_HEADER",
            header_name="Authorization",
            value="Bearer xxx",
        )
        assert rule.domains == ["*.api.com"]
        assert rule.action == "SET_HEADER"
        assert rule.header_name == "Authorization"
        assert rule.value == "Bearer xxx"

    def test_extra_fields_allowed(self):
        rule = EncryptableHeaderRule.model_validate(
            {
                "domains": ["*.test.com"],
                "action": "SET_HEADER",
                "header_name": "X-Custom",
                "value": "val",
                "extra_field": "extra",
            }
        )
        assert rule.extra_field == "extra"


class TestEncryptableOutBoundRule:
    """Test EncryptableOutBoundRule model."""

    def test_default_empty_rules(self):
        rule = EncryptableOutBoundRule()
        assert rule.header_operation_rules == []

    def test_with_rules(self):
        header_rule = EncryptableHeaderRule(
            domains=["*.api.com"],
            action="SET_HEADER",
            header_name="Authorization",
            value="Bearer xxx",
            encrypt_value=True,
        )
        rule = EncryptableOutBoundRule(header_operation_rules=[header_rule])
        assert len(rule.header_operation_rules) == 1
        assert rule.header_operation_rules[0].encrypt_value is True

    def test_extra_fields_allowed(self):
        rule = EncryptableOutBoundRule.model_validate(
            {
                "header_operation_rules": [],
                "custom": "val",
            }
        )
        assert rule.custom == "val"

    def test_multiple_rules(self):
        rules = [
            EncryptableHeaderRule(
                domains=["*.a.com"],
                action="SET_HEADER",
                header_name="X-A",
                value="a",
            ),
            EncryptableHeaderRule(
                domains=["*.b.com"],
                action="SET_HEADER",
                header_name="X-B",
                value="b",
                encrypt_value=True,
            ),
        ]
        rule = EncryptableOutBoundRule(header_operation_rules=rules)
        assert len(rule.header_operation_rules) == 2
