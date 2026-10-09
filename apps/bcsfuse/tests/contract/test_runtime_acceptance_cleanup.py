"""Acceptance cleanup retries only IDs owned by the current run."""

import httpx
import pytest

from tests.fixtures.runtime_acceptance import RuntimeAcceptance


def test_cleanup_retries_transient_failure_and_tolerates_already_deleted():
    attempts = {}

    def serve(request):
        path = request.url.path
        attempts[path] = attempts.get(path, 0) + 1
        if path.endswith("owned-first") and attempts[path] == 1:
            return httpx.Response(503)
        return httpx.Response(404 if path.endswith("owned-second") else 200)

    with httpx.Client(transport=httpx.MockTransport(serve), base_url="http://isolated") as client:
        acceptance = RuntimeAcceptance(client, "test")
        acceptance.worker_ids = ["owned-first", "owned-second"]
        acceptance.cleanup()
    assert attempts == {"/v1/workers/owned-first": 2, "/v1/workers/owned-second": 1}


def test_cleanup_reports_failure_after_bounded_retries():
    attempts = []

    def serve(request):
        attempts.append(request.url.path)
        return httpx.Response(500)

    with httpx.Client(transport=httpx.MockTransport(serve), base_url="http://isolated") as client:
        acceptance = RuntimeAcceptance(client, "test")
        acceptance.worker_ids = ["owned-first"]
        with pytest.raises(AssertionError, match="owned-first: HTTP 500"):
            acceptance.cleanup()
    assert attempts == ["/v1/workers/owned-first"] * 2
