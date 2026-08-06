"""Unit tests for redact_utils — credential egress sanitizer.

Ensures thetaKey ciphertext and other credential fields never reach logs or
API responses. Covers key normalization variants, recursion, copy semantics,
and log_safe_model conversion paths.
"""

import pytest
from pydantic import BaseModel

from secbaas.community.core.utils.redact_utils import (
    SENSITIVE_LOG_KEYS,
    log_safe_model,
    redact_sensitive,
)


class _Sample(BaseModel):
    name: str
    api_key: str
    nested: dict
    extra_properties: dict | None = None


class TestRedactSensitive:
    """redact_sensitive — recursive key-normalized redaction."""

    def test_sensitive_key_variants_normalized(self):
        """Separator/case variants of sensitive keys are all redacted."""
        payload = {
            "extra_properties": {"aicoding": {"theta_key": "enc:v1:SECRET"}},
            "extra-properties": "x",
            "Extra_Properties": "y",
            "apiKey": "AK-PLAIN",
            "API_KEY": "AK2",
            "api-key": "AK3",
            "Authorization": "Bearer TOKEN",
            "authorization": "bearer t2",
            "token": "TOK",
            "TOKEN": "TOK2",
        }
        out = redact_sensitive(payload)
        for key in payload:
            assert out[key] == "<redacted>", f"key {key!r} not redacted"
        assert "enc:v1:SECRET" not in str(out)
        assert "AK-PLAIN" not in str(out)
        assert "Bearer TOKEN" not in str(out)

    def test_nested_dict_list_tuple_walked(self):
        payload = {
            "outer": [
                {"api_key": "list-ak", "safe": "keep"},
                {"nested": {"token": "deep-tok"}},
            ],
            "matrix": ({"authorization": "tup-auth"},),
            "plain": "scalar",
        }
        out = redact_sensitive(payload)
        assert out["outer"][0]["api_key"] == "<redacted>"
        assert out["outer"][0]["safe"] == "keep"
        assert out["outer"][1]["nested"]["token"] == "<redacted>"
        assert out["matrix"][0]["authorization"] == "<redacted>"
        assert out["plain"] == "scalar"

    def test_scalar_unchanged(self):
        assert redact_sensitive("plain") == "plain"
        assert redact_sensitive(42) == 42

    def test_empty_collections(self):
        assert redact_sensitive({}) == {}
        assert redact_sensitive([]) == []

    def test_input_not_mutated(self):
        payload = {"api_key": "secret", "nested": {"token": "t"}}
        original = {"api_key": "secret", "nested": {"token": "t"}}
        redact_sensitive(payload)
        assert payload == original

    def test_sensitive_keys_set_contents(self):
        assert "extraproperties" in SENSITIVE_LOG_KEYS
        assert "apikey" in SENSITIVE_LOG_KEYS
        assert "authorization" in SENSITIVE_LOG_KEYS
        assert "token" in SENSITIVE_LOG_KEYS


class TestLogSafeModel:
    """log_safe_model — model/dict/scalar to redacted log structure."""

    def test_pydantic_model_redacted(self):
        sample = _Sample(
            name="bot",
            api_key="PLAIN-AK",
            nested={"token": "NESTED-TOK"},
            extra_properties={"aicoding": {"theta_key": "enc:v1:CT"}},
        )
        out = log_safe_model(sample)
        assert out["name"] == "bot"
        assert out["api_key"] == "<redacted>"
        assert out["nested"]["token"] == "<redacted>"
        assert out["extra_properties"] == "<redacted>"
        assert "PLAIN-AK" not in str(out)
        assert "enc:v1:CT" not in str(out)

    def test_plain_dict_redacted(self):
        out = log_safe_model({"api_key": "AK", "ok": "v"})
        assert out["api_key"] == "<redacted>"
        assert out["ok"] == "v"

    def test_scalar_uses_repr(self):
        out = log_safe_model("a string")
        assert isinstance(out, str)
        assert "a string" in out

    def test_broken_model_returns_safe_envelope(self):
        class Broken(BaseModel):
            name: str

            def model_dump(self, *a, **k):  # type: ignore[override]
                raise RuntimeError("boom")

        out = log_safe_model(Broken(name="x"))
        assert out["value_type"] == "Broken"
        assert out["dump_error_type"] == "RuntimeError"
        assert "x" not in str(out)
