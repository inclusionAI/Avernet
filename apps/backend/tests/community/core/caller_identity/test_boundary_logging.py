"""Large external payloads retain bounded, verifiable diagnostics."""
import hashlib

import pytest

from agentclaw.community.core.caller_identity.boundary_logging import redact_boundary


@pytest.mark.parametrize("payload", [b"binary-response\x00", "large-business-response-" * 512])
def test_large_or_binary_payload_reports_size_and_digest_instead_of_raw_data(payload):
    result = redact_boundary({"response": payload})
    raw = payload if isinstance(payload, bytes) else payload.encode()
    assert result == {"response": {
        "type": type(payload).__name__,
        "length": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }}
    assert payload not in result.values()
