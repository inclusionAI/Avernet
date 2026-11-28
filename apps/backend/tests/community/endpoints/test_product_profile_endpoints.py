"""Registered happy/error cases drive real product services through the full app."""

from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)
from tests.community.endpoints.product_profile_seeds import (
    seed_service,
    seed_profile,
    seed_collet,
    seed_intervention,
    PROFILE_BODY,
)

BASE = "/api/v1/collaboration/task/products"
AUTH = {"x-user-id": "u1"}


@endpoint_test(
    method="POST",
    path=BASE + "/profiles",
    scenario="happy",
    seed=seed_service,
    input=CaseInput(headers=AUTH, json_body=PROFILE_BODY),
    expect=ExpectSuccess(
        status=200, json_contains={"data": {"name": "endpoint-profile", "id": 1}}
    ),
)
def create_profile_happy():
    """Authorized management persists a real immutable configuration."""


@endpoint_test(
    method="POST",
    path=BASE + "/profiles",
    scenario="invalid_yaml",
    seed=seed_service,
    input=CaseInput(headers=AUTH, json_body={**PROFILE_BODY, "stages": "stages: []"}),
    expect=ExpectError(status=422),
)
def create_profile_invalid_yaml():
    """The actual YAML compiler rejects an empty graph."""


@endpoint_test(
    method="POST",
    path=BASE + "/collets",
    scenario="happy",
    seed=seed_profile,
    input=CaseInput(
        headers=AUTH,
        json_body={"profile_id": 1, "content": "request"},
    ),
    expect=ExpectSuccess(
        status=200,
        json_contains={"data": {"id": 1, "profile_id": 1, "status": "CREATE"}},
    ),
)
def create_collet_happy():
    """The real SQL unit of work creates Collet/rollout/stage together."""


@endpoint_test(
    method="POST",
    path=BASE + "/collets",
    scenario="missing_profile",
    seed=seed_service,
    input=CaseInput(
        headers=AUTH,
        json_body={"profile_id": 999, "content": "request"},
    ),
    expect=ExpectError(status=404),
)
def create_collet_missing():
    """A missing scoped Profile cannot create a runnable collaboration."""


@endpoint_test(
    method="GET",
    path=BASE + "/collets/{collet_id}/rollouts",
    scenario="happy",
    seed=seed_collet,
    input=CaseInput(
        headers=AUTH,
        path_params={"collet_id": 1},
        query_params={"all_rollout": "false"},
    ),
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "data": {
                "revision": 1,
                "current_rollout_id": 2,
                "rollouts": [{"collet_id": 1, "rollout_id": 2}],
            }
        },
    ),
)
def query_rollouts_happy():
    """Current rollout comes from the stored pointer."""


@endpoint_test(
    method="GET",
    path=BASE + "/collets/{collet_id}/rollouts",
    scenario="missing",
    seed=seed_service,
    input=CaseInput(headers=AUTH, path_params={"collet_id": 999}),
    expect=ExpectError(status=404),
)
def query_rollouts_missing():
    """Queries never create a missing Collet or rollout."""


@endpoint_test(
    method="POST",
    path=BASE + "/rollouts/{rollout_id}/interactive",
    scenario="happy",
    seed=seed_collet,
    input=CaseInput(
        headers=AUTH,
        path_params={"rollout_id": 2},
        json_body={
            "interactive_uuid": "endpoint-review",
            "interactive_context": "review",
        },
    ),
    expect=ExpectSuccess(status=200, json_contains={"data": {"type": "DO_NOTHING"}}),
)
def interactive_happy():
    """The default interaction is explicitly conservative."""


@endpoint_test(
    method="POST",
    path=BASE + "/rollouts/{rollout_id}/interactive",
    scenario="missing_interactive_uuid",
    seed=seed_collet,
    input=CaseInput(
        headers=AUTH,
        path_params={"rollout_id": 2},
        json_body={
            "interactive_context": "review",
        },
    ),
    expect=ExpectError(status=422),
)
def interactive_missing_uuid():
    """Every intervention requires an explicit idempotency identity."""


@endpoint_test(
    method="GET",
    path=BASE + "/stages/{stage_rollout_id}/messages",
    scenario="happy_unbound",
    seed=seed_collet,
    input=CaseInput(headers=AUTH, path_params={"stage_rollout_id": 3}),
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "data": {
                "stage_rollout_id": 3,
                "session_id": "",
                "bound": False,
                "messages": [],
            }
        },
    ),
)
def messages_happy():
    """No logical session is fabricated before dispatch."""


@endpoint_test(
    method="GET",
    path=BASE + "/stages/{stage_rollout_id}/messages",
    scenario="missing",
    seed=seed_service,
    input=CaseInput(headers=AUTH, path_params={"stage_rollout_id": 999}),
    expect=ExpectError(status=404),
)
def messages_missing():
    """Unknown stages never trigger an arbitrary provider read."""


@endpoint_test(
    method="POST",
    path=BASE + "/rollouts/{rollout_id}/interactive",
    scenario="uuid_content_conflict",
    seed=seed_intervention,
    input=CaseInput(
        headers=AUTH,
        path_params={"rollout_id": 2},
        json_body={
            "interactive_uuid": "endpoint-intervention",
            "interactive_context": "different feedback",
        },
    ),
    expect=ExpectError(status=409),
)
def interactive_uuid_content_conflict():
    """A committed intervention UUID cannot be repurposed with new content."""
