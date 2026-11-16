"""OpenAPI v1 endpoint cases for service-Bot unified evaluation.

The cases run the real public router, the public permission seam, the real
ServiceBotEvalService, and the community evaluation plugin. They keep all
service-Bot evaluation OpenAPI routes inside endpoint coverage's happy + error requirement.
"""

from __future__ import annotations

import time

import jwt

from agentclaw.community.adapters.http.openapi_v1.dependencies import PRINCIPAL_HEADER
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.repository.protocols.publishing import (
    BotPublishRepositoryProtocol,
)
from agentclaw.community.core.service_bot.repository.models import PublishStatus
from agentclaw.community.utils.gateway_principal_config import (
    init_principal_verifier_config,
)
from tests.community.factories.access import make_staff_user
from agentclaw.community.plugin_api.http_client import HttpClient, QUALIFIER_MASA_AGENT_EVAL
from typing import Annotated
from tests.community.factories.quality import make_quality_task
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
    json_response,
)

_OWNER = "u_eval_openapi"
_BOT_ID = "service_eval_openapi_bot"
_VERSION = "1"
_SIGNING_KEY = "service-bot-eval-openapi-signing-key-32bytes"
_QUERY = {"user_id": _OWNER, "owner_id": _OWNER}


class _Secret:
    secret_user = "test"
    secret_value = _SIGNING_KEY


class _Resolver:
    def get_secret(self, _secret_name: str) -> _Secret:
        return _Secret()


def _principal() -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "iss": "gateway",
            "aud": "backend",
            "iat": now,
            "exp": now + 3600,
            "principals": [
                {
                    "type": "user",
                    "subject": {"id": _OWNER, "username": "u_eval_openapi@example.test"},
                }
            ],
        },
        _SIGNING_KEY,
        algorithm="HS256",
    )


_HEADERS = {PRINCIPAL_HEADER: _principal()}


def _seed_principal(world) -> None:
    init_principal_verifier_config(_Resolver(), "test-key", strict=False)
    make_staff_user(world, user_id=_OWNER)


def _seed_service_bot(world) -> None:
    """Seed the owner, a service Bot, its publication record, and MASA sample sets."""
    _seed_principal(world)
    masa_eval_http = world.get(Annotated[HttpClient, QUALIFIER_MASA_AGENT_EVAL])
    masa_eval_http.set_response(
        "get",
        json_response(
            {
                "support": True,
                "sets": [
                    {
                        "uuid": "set-standard-1",
                        "name": "标准评测样本集",
                        "description": "标准评测样本集",
                        "sample_count": 300,
                    }
                ],
            }
        ),
    )
    bot = world.get(BotRepository).insert(
        {
            "bot_id": _BOT_ID,
            "bot_name": "Evaluation OpenAPI Bot",
            "bot_desc": "Evaluation public contract smoke bot",
            "owner_id": _OWNER,
            "entity_id": _OWNER,
            "entity_type": "staff",
            "creator_id": _OWNER,
            "bot_type": "service",
            "status": "ACTIVE",
            "active_engine": "openclaw",
        }
    )
    world.get(BotPublishRepositoryProtocol).insert(
        {
            "source_bot_pk": bot["id"],
            "source_bot_id": _BOT_ID,
            "publish_bot_id": _BOT_ID,
            "name": "Evaluation OpenAPI Bot",
            "owner_id": _OWNER,
            "permission_owner": _OWNER,
            "status": PublishStatus.DRAFT,
            "version": int(_VERSION),
            "env": "dev",
        }
    )


def _cases(method: str, path: str, *, happy: dict, input_kwargs: dict | None = None):
    """Build the happy/error pair; all six routes share one error shape."""
    return method, path, happy, input_kwargs or {}


_ROUTES: list[tuple[str, str, dict, dict]] = [
    _cases(
        "GET",
        "/openapi/v1/bots/{bot_id}/eval/submissions/{submission_id}",
        happy={"data": {"submission_id": "submission-001"}},
        input_kwargs={"path_params": {"bot_id": _BOT_ID, "submission_id": "submission-001"}},
    ),
    _cases(
        "GET",
        "/openapi/v1/bots/{bot_id}/eval/versions/{bot_version}",
        happy={
            "data": {
                "owner_permission": {"operable": True},
                "tabs": {
                    "simple_cert_submit": {"visible": False},
                    "restart_eval": {"visible": False},
                    "eval_history": {"visible": True},
                },
            }
        },
    ),
    _cases(
        "POST",
        "/openapi/v1/bots/{bot_id}/eval/versions/{bot_version}/simple-cert/precheck",
        happy={"data": {"gate_status": "SKIPPED", "allowed": True}},
    ),
    _cases(
        "POST",
        "/openapi/v1/bots/{bot_id}/eval/versions/{bot_version}/simple-cert/sync",
        happy={"data": {"gate_status": "SKIPPED", "allowed": True}},
    ),
    _cases(
        "GET",
        "/openapi/v1/bots/{bot_id}/eval/versions/{bot_version}/sample-sets",
        happy={"data": {"support": True, "sets": [{"uuid": "set-standard-1"}]}},
    ),
]


def _endpoint_case(method, path, happy, input_kwargs, *, happy_case):
    input_data = {
        "path_params": {"bot_id": _BOT_ID, "bot_version": _VERSION},
        "query_params": _QUERY,
        "headers": _HEADERS,
        **input_kwargs,
    }
    decorator = endpoint_test(
        method=method,
        path=path,
        scenario="happy" if happy_case else "bot_not_found",
        input=CaseInput(**input_data),
        seed=_seed_service_bot if happy_case else _seed_principal,
        expect=ExpectSuccess(status=200, json_contains={"code": 200000, **happy})
        if happy_case
        else ExpectError(status=404, json_contains={"message": "Not found", "data": None}),
    )
    return decorator


def _define_route_cases() -> None:
    for method, path, happy, input_kwargs in _ROUTES:
        for happy_case in (True, False):
            decorator = _endpoint_case(
                method,
                path,
                happy,
                input_kwargs,
                happy_case=happy_case,
            )

            def case() -> None:
                pass

            case.__name__ = (
                f"{path.rsplit('/', 1)[-1].replace('{', '').replace('}', '')}_"
                f"{'happy' if happy_case else 'error'}"
            )
            decorator(case)


_define_route_cases()


def _history_cases() -> None:
    path = "/openapi/v1/bots/{bot_id}/eval/versions/{bot_version}/history"
    query = _QUERY | {"page_no": 1, "page_size": 10}
    for happy_case, seed in ((True, _seed_service_bot), (False, _seed_principal)):
        @endpoint_test(
            method="GET",
            path=path,
            scenario="happy" if happy_case else "bot_not_found",
            input=CaseInput(
                path_params={"bot_id": _BOT_ID, "bot_version": _VERSION},
                query_params=query,
                headers=_HEADERS,
            ),
            seed=seed,
            expect=ExpectSuccess(
                status=200,
                json_contains={
                    "code": 200000,
                    "data": {"records": [], "page": {"page_no": 1, "page_size": 10, "total": 0}},
                },
            )
            if happy_case
            else ExpectError(status=404, json_contains={"message": "Not found", "data": None}),
        )
        def history_case() -> None:
            pass

        history_case.__name__ = "eval_history_happy" if happy_case else "eval_history_error"


_history_cases()


def _submission_cases() -> None:
    path = "/openapi/v1/bots/{bot_id}/eval/versions/{bot_version}/simple-cert/submissions"
    for happy_case, seed in ((True, _seed_service_bot), (False, _seed_principal)):
        @endpoint_test(
            method="POST",
            path=path,
            scenario="happy" if happy_case else "bot_not_found",
            input=CaseInput(
                path_params={"bot_id": _BOT_ID, "bot_version": _VERSION},
                query_params=_QUERY,
                headers=_HEADERS,
                form_data={"trigger_source": "manual_simple_cert"},
                files=[
                    ("sample_files", ("samples.jsonl", b'{"question":"hello"}\n')),
                    ("report_files", ("report.json", b'{"result":"PASS"}')),
                ],
            ),
            seed=seed,
            expect=ExpectSuccess(
                status=200,
                json_contains={"code": 200000, "message": "评测数据已提交，等待认证结果"},
            )
            if happy_case
            else ExpectError(status=404, json_contains={"message": "Not found", "data": None}),
        )
        def submission_case() -> None:
            pass

        submission_case.__name__ = "eval_submission_happy" if happy_case else "eval_submission_error"


_submission_cases()


def _standard_task_cases() -> None:
    create_path = "/openapi/v1/bots/{bot_id}/eval/versions/{bot_version}/tasks"
    current_path = create_path + "/current"
    body = {
        "set_uuid": "set-standard-1",
        "goal_type": "json",
        "goal_json": {"metric": "accuracy"},
        "trigger_source": "manual_restart_eval",
    }
    for happy_case, seed in ((True, _seed_service_bot), (False, _seed_principal)):
        @endpoint_test(
            method="POST",
            path=create_path,
            scenario="happy" if happy_case else "bot_not_found",
            input=CaseInput(
                path_params={"bot_id": _BOT_ID, "bot_version": _VERSION},
                query_params=_QUERY,
                headers=_HEADERS,
                json_body=body,
            ),
            seed=seed,
            expect=ExpectSuccess(
                status=200,
                json_contains={
                    "code": 200000,
                    "message": "标准评测任务已创建",
                    "data": {"task_type": "eval", "set_uuid": "set-standard-1"},
                },
            )
            if happy_case
            else ExpectError(status=404, json_contains={"message": "Not found", "data": None}),
        )
        def create_standard_task_case() -> None:
            pass

        create_standard_task_case.__name__ = (
            "standard_eval_create_task_happy" if happy_case else "standard_eval_create_task_error"
        )

    def _seed_current_task(world) -> None:
        _seed_service_bot(world)
        make_quality_task(
            world,
            bot_id=_BOT_ID,
            owner_id=_OWNER,
            status="success",
            ext={
                "source": "standard_eval",
                "version": int(_VERSION),
                "set_uuid": "set-standard-1",
                "sample_set_name": "标准评测样本集",
                "sample_set_count": 300,
                "standard_result_saved": True,
                "eval_progress": {
                    "status": "completed",
                    "report": {"reportUrl": "https://reports.example.test/report"},
                },
            },
        )

    for happy_case, seed in ((True, _seed_current_task), (False, _seed_principal)):
        @endpoint_test(
            method="GET",
            path=current_path,
            scenario="happy" if happy_case else "bot_not_found",
            input=CaseInput(
                path_params={"bot_id": _BOT_ID, "bot_version": _VERSION},
                query_params=_QUERY,
                headers=_HEADERS,
            ),
            seed=seed,
            expect=ExpectSuccess(
                status=200,
                json_contains={
                    "code": 200000,
                    "data": {
                        "status": "success",
                        "set_uuid": "set-standard-1",
                        "report_url": "https://reports.example.test/report",
                    },
                },
            )
            if happy_case
            else ExpectError(status=404, json_contains={"message": "Not found", "data": None}),
        )
        def current_standard_task_case() -> None:
            pass

        current_standard_task_case.__name__ = (
            "standard_eval_current_task_happy" if happy_case else "standard_eval_current_task_error"
        )


_standard_task_cases()


def _standard_environment_cases() -> None:
    create_path = "/openapi/v1/bots/{bot_id}/eval/versions/{bot_version}/environments"
    current_path = create_path + "/current"
    create_body = {"default_tag": "eval", "ttl_minutes": 120}
    for happy_case, seed in ((True, _seed_service_bot), (False, _seed_principal)):
        @endpoint_test(
            method="POST",
            path=create_path,
            scenario="happy" if happy_case else "bot_not_found",
            input=CaseInput(
                path_params={"bot_id": _BOT_ID, "bot_version": _VERSION},
                query_params=_QUERY,
                headers=_HEADERS,
                json_body=create_body,
            ),
            seed=seed,
            expect=ExpectSuccess(
                status=200,
                json_contains={
                    "code": 200000,
                    "message": "评测环境创建请求已提交，请轮询构建状态",
                    "data": {"default_tag": "eval", "build_status": "not_supported"},
                },
            )
            if happy_case
            else ExpectError(status=404, json_contains={"message": "Not found", "data": None}),
        )
        def create_environment_case() -> None:
            pass

        create_environment_case.__name__ = (
            "standard_eval_create_environment_happy" if happy_case else "standard_eval_create_environment_error"
        )

    for happy_case, seed in ((True, _seed_service_bot), (False, _seed_principal)):
        @endpoint_test(
            method="GET",
            path=current_path,
            scenario="happy" if happy_case else "bot_not_found",
            input=CaseInput(
                path_params={"bot_id": _BOT_ID, "bot_version": _VERSION},
                query_params=_QUERY,
                headers=_HEADERS,
            ),
            seed=seed,
            expect=ExpectSuccess(
                status=200,
                json_contains={
                    "code": 200000,
                    "data": {"supported": False, "build_status": "not_supported"},
                },
            )
            if happy_case
            else ExpectError(status=404, json_contains={"message": "Not found", "data": None}),
        )
        def current_environment_case() -> None:
            pass

        current_environment_case.__name__ = (
            "standard_eval_current_environment_happy" if happy_case else "standard_eval_current_environment_error"
        )

    for happy_case, seed in ((True, _seed_service_bot), (False, _seed_principal)):
        @endpoint_test(
            method="DELETE",
            path=current_path,
            scenario="happy" if happy_case else "bot_not_found",
            input=CaseInput(
                path_params={"bot_id": _BOT_ID, "bot_version": _VERSION},
                query_params=_QUERY | {"reason": "standard_eval_finished"},
                headers=_HEADERS,
            ),
            seed=seed,
            expect=ExpectSuccess(
                status=200,
                json_contains={
                    "code": 200000,
                    "message": "评测环境销毁请求已处理",
                    "data": {"destroyed": False, "destroy_status": "skipped"},
                },
            )
            if happy_case
            else ExpectError(status=404, json_contains={"message": "Not found", "data": None}),
        )
        def destroy_environment_case() -> None:
            pass

        destroy_environment_case.__name__ = (
            "standard_eval_destroy_environment_happy" if happy_case else "standard_eval_destroy_environment_error"
        )


_standard_environment_cases()


def _standard_result_cases() -> None:
    path = "/openapi/v1/bots/{bot_id}/eval/versions/{bot_version}/standard-eval/results"
    body = {
        "task_id": 1,
        "task_uuid": "quality-task-1",
        "task_status": "success",
        "gate_status": "PASSED",
        "trigger_source": "manual_restart_standard_eval",
        "sample_set_uuid": "set-1",
        "sample_set_name": "标准样本集",
        "sample_count": 10,
        "report_url": "https://reports.example.test/report",
        "progress": {"set_task_uuid": "masa-task-1"},
    }
    for happy_case, seed in ((True, _seed_service_bot), (False, _seed_principal)):

        @endpoint_test(
            method="POST",
            path=path,
            scenario="happy" if happy_case else "bot_not_found",
            input=CaseInput(
                path_params={"bot_id": _BOT_ID, "bot_version": _VERSION},
                query_params=_QUERY,
                headers=_HEADERS,
                json_body=body,
            ),
            seed=seed,
            expect=ExpectSuccess(
                status=200,
                json_contains={
                    "code": 200000,
                    "message": "标准评测结果已保存",
                    "data": {"saved": False},
                },
            )
            if happy_case
            else ExpectError(status=404, json_contains={"message": "Not found", "data": None}),
        )
        def standard_result_case() -> None:
            pass

        standard_result_case.__name__ = (
            "standard_eval_result_happy" if happy_case else "standard_eval_result_error"
        )


_standard_result_cases()
