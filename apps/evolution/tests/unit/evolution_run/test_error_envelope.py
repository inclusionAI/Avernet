"""09 §2.1：领域错误码要能被客户端读到，且不改变既有信封字段。"""

from __future__ import annotations

import json
from types import SimpleNamespace

from avernet_evolution.adapters.control import response
from avernet_evolution.core.evolution_run.api import error_envelope
from avernet_evolution.core.evolution_run.errors import GenomeBaselineMissing


def test_missing_baseline_maps_to_conflict_with_domain_code() -> None:
    envelope = error_envelope(GenomeBaselineMissing("bot-1"))
    assert envelope["status"] == 409
    assert envelope["body"]["error"]["code"] == "no_genome_baseline"
    assert "bot-1" in envelope["body"]["error"]["message"]


def test_http_envelope_keeps_code_and_adds_error_member() -> None:
    request = SimpleNamespace(state=SimpleNamespace(trace_id="trace-1"))
    result = response(
        request, status=409, error={"code": "no_genome_baseline", "message": "m", "details": []}
    )
    body = json.loads(result.body)
    assert body["code"] == 409000
    assert body["error"]["code"] == "no_genome_baseline"
    assert result.headers["X-Trace-Id"] == "trace-1"


def test_success_envelope_has_no_error_member() -> None:
    request = SimpleNamespace(state=SimpleNamespace(trace_id="t"))
    body = json.loads(response(request, {"ok": True}).body)
    assert "error" not in body
