"""Per-endpoint dependency-injection test framework.

Authors declare endpoint cases; the framework owns invocation and
expectation checks. See ``tests/framework/README.md`` (added later in
this feature) for the author guide.
"""
from tests.community.framework.case import (
    CaseInput,
    EndpointCase,
    Expectation,
    ExpectError,
    ExpectSuccess,
)
from tests.community.framework.di_seams import (
    bind_failing_method,
    bind_method,
    bind_overrides,
)
from tests.community.framework.endpoint_helpers import (
    StubResponse,
    drain_background_tasks,
    http_envelope_response,
    json_response,
)
from tests.community.framework.registry import ENDPOINT_CASES, endpoint_test


__all__ = [
    "CaseInput",
    "EndpointCase",
    "ExpectError",
    "ExpectSuccess",
    "Expectation",
    "ENDPOINT_CASES",
    "endpoint_test",
    "bind_failing_method",
    "bind_method",
    "bind_overrides",
    "StubResponse",
    "drain_background_tasks",
    "http_envelope_response",
    "json_response",
]
