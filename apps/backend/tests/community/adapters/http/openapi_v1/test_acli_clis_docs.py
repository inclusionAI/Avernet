"""The acli CLI writes document both answers they give, with samples that hold.

The samples are checked as declared: rendering the document drops ``null``
values from every example, so the rendered ones only show that they are there.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI
from starlette.requests import Request

from agentclaw.community.adapters.http.openapi_v1.bots.acli_clis import add_bot_cli, router
from agentclaw.community.adapters.http.openapi_v1.bots.schemas_acli_clis_responses import (
    ADD_RESPONSES,
    CANCEL_RESPONSES,
    REMOVE_RESPONSES,
)
from agentclaw.community.adapters.http.openapi_v1.bots.schemas_acli_clis import (
    AcliCliOperation,
    AddBotCli,
    AddedBotCli,
    CliOperationConflict,
)
from agentclaw.community.adapters.http.openapi_v1.contracts import Deleted, Envelope
from agentclaw.community.api.acli_cli_service import (
    AcliAction,
    AcliOperation,
    AcliOperationInProgressError,
    OperationKind,
    OperationStatus,
    OperationStep,
)
from agentclaw.community.core.service_bot.types import PublishStage

_WRITES = {
    ("post", "/openapi/v1/bots/{bot_id}/clis"): (AddedBotCli, ADD_RESPONSES),
    ("delete", "/openapi/v1/bots/{bot_id}/clis/{cli_code}"): (Deleted, REMOVE_RESPONSES),
    ("post", "/openapi/v1/bots/{bot_id}/clis/{cli_code}/install/cancel"):
        (AcliCliOperation, CANCEL_RESPONSES),
    ("post", "/openapi/v1/bots/{bot_id}/clis/{cli_code}/uninstall/cancel"):
        (AcliCliOperation, CANCEL_RESPONSES),
}


@pytest.fixture(scope="module")
def paths() -> dict:
    app = FastAPI()
    app.include_router(router)
    return app.openapi()["paths"]


def _example(responses: dict, status: int | str) -> dict:
    return responses[status]["content"]["application/json"]["example"]


@pytest.mark.parametrize(("method", "path"), list(_WRITES))
def test_a_write_says_how_it_runs(paths, method, path) -> None:
    description = paths[path][method]["description"]

    assert "**How it runs.**" in description
    assert "`async=true`" in description and "cli-operations/{operation_id}" in description


@pytest.mark.parametrize(("method", "path"), list(_WRITES))
def test_a_write_samples_both_answers(paths, method, path) -> None:
    model, responses = _WRITES[(method, path)]
    done, running = _example(responses, 200), _example(responses, 202)

    Envelope[model].model_validate(done)
    assert (done["code"], done["message"]) == (200000, "OK")
    Envelope[AcliCliOperation].model_validate(running)
    assert (running["code"], running["message"]) == (202000, "Accepted")
    assert running["data"]["status"] == "running" and running["data"]["step"]
    rendered = paths[path][method]["responses"]
    assert _example(rendered, "202")["code"] == 202000
    assert _example(rendered, "200")["code"] == 200000


@pytest.mark.parametrize("responses", [ADD_RESPONSES, REMOVE_RESPONSES])
def test_add_and_remove_sample_the_busy_conflict(responses) -> None:
    Envelope[CliOperationConflict].model_validate(_example(responses, 409))


def test_a_cancel_is_never_documented_as_busy() -> None:
    assert 409 not in CANCEL_RESPONSES


@pytest.mark.asyncio
async def test_the_busy_sample_matches_the_real_answer() -> None:
    running = AcliOperation(
        id="a" * 32, kind=OperationKind.OPERATION, action=AcliAction.INSTALL,
        cli_code="yuque-cli", stage=PublishStage.DRAFT, status=OperationStatus.RUNNING,
        step=OperationStep.INSTALLING, error_code=None, error_message=None,
    )
    service = AsyncMock()
    service.add.side_effect = AcliOperationInProgressError(running)
    locks = Mock()
    locks.get_lock_info.return_value = Mock(lock=None)
    request = Request({"type": "http", "method": "POST", "path": "/"})
    request.state.trace_id = "trace-acli"

    response = await add_bot_cli(
        "bot-1", AddBotCli(cli_code="yuque-cli"), request, "owner", "owner", None, False,
        service, locks,
    )
    real = json.loads(response.body)
    sample = _example(ADD_RESPONSES, 409)

    assert response.status_code == 409
    assert (real["code"], real["message"]) == (sample["code"], sample["message"])
    assert real["data"].keys() == sample["data"].keys()
    assert real["data"]["operation"].keys() == sample["data"]["operation"].keys()
