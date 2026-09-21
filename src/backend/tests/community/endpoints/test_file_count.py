"""Registered endpoint cases traverse HTTP, auth, real storage and DI."""
from tests.community.factories.file_count import seed_file_count, assert_file_count_result
from tests.community.framework import CaseInput, ExpectError, ExpectSuccess, endpoint_test

_PATH = "/api/service-bot/publish/ops/file-count"
_QUERY = {"bot_id": "ignore-bot", "entity_id": "ignore_owner", "stage": "draft", "path": "/workspace"}
_INPUT = CaseInput(headers={"x-user-id": "ignore_owner"}, query_params=_QUERY)


@endpoint_test(method="GET", path=_PATH, scenario="happy", input=_INPUT,
               seed=seed_file_count, expect=ExpectSuccess(status=200, json_contains={"success": True}),
               extra_assertions=(assert_file_count_result,))
def file_count_happy():
    """Authorized owner counts a real current draft binding."""


@endpoint_test(method="GET", path=_PATH, scenario="engine_failed", input=_INPUT,
               seed=lambda world: seed_file_count(world, success=False),
               expect=ExpectError(status=200, json_contains={"success": False}),
               extra_assertions=(assert_file_count_result,))
def file_count_error():
    """Engine rejection preserves null and fails the envelope."""


@endpoint_test(method="GET", path=_PATH, scenario="missing_stage",
               input=CaseInput(headers={"x-user-id": "ignore_owner"},
                               query_params={k: v for k, v in _QUERY.items() if k != "stage"}),
               seed=seed_file_count, expect=ExpectError(status=422))
def file_count_requires_stage():
    """Never silently selects draft when stage is absent."""


@endpoint_test(method="GET", path=_PATH, scenario="invalid_path",
               input=CaseInput(headers={"x-user-id": "ignore_owner"},
                               query_params={**_QUERY, "path": ""}),
               seed=seed_file_count, expect=ExpectError(status=422))
def file_count_requires_path():
    """Empty path cannot become an implicit filesystem root."""
