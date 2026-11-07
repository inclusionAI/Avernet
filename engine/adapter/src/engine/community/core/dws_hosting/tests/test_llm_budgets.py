"""DWS auxiliary deadlines leave room for the model and HTTP response."""

import asyncio
from types import SimpleNamespace

import pytest

from engine.community.core.dws_hosting import decision_repair, reply_review, scope
from engine.community.core.dws_hosting.execution import invocation_for
from .test_decision_repair import Repair
from .test_execution import Engine, build_execution, result_json
from .test_reply_review import candidate


@pytest.mark.parametrize("stage", ["scope", "review", "repair"])
async def test_auxiliary_deadline_exceeds_http_budget(tmp_path, monkeypatch, stage):
    request = invocation_for(build_execution(tmp_path, text="hi")[2])
    module = {"scope": scope, "review": reply_review, "repair": decision_repair}[stage]
    deadlines = []

    def timeout(seconds):
        deadlines.append(seconds)
        assert seconds == 70
        assert seconds > 65 > 60
        return asyncio.timeout(seconds)

    monkeypatch.setattr(module, "asyncio", SimpleNamespace(timeout=timeout))
    if stage == "scope":
        result = await scope.classify_scope(Engine(), request, 1)
        assert result.decision == "allow"
    elif stage == "review":
        result = await reply_review.review_reply(Engine(), request, candidate())
        assert result.decision.action == "reply"
    else:
        result = await decision_repair.recover_decision(
            Repair(), request, result_json("A plain reply."), 1
        )
        assert result.action == "reply"
    assert deadlines == [70]
