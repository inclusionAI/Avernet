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
        json_body={"profile_id": 1, "content": "request", "request_id": "new-collet"},
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
        json_body={"profile_id": 999, "content": "request", "request_id": "missing"},
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
            "interactive_context": "review",
            "request_id": "feedback",
            "expected_revision": 1,
        },
    ),
    expect=ExpectSuccess(status=200, json_contains={"data": {"type": "DO_NOTHING"}}),
)
def interactive_happy():
    """The default interaction is explicitly conservative."""


@endpoint_test(
    method="POST",
    path=BASE + "/rollouts/{rollout_id}/interactive",
    scenario="stale",
    seed=seed_collet,
    input=CaseInput(
        headers=AUTH,
        path_params={"rollout_id": 2},
        json_body={
            "interactive_context": "review",
            "request_id": "feedback",
            "expected_revision": 999,
        },
    ),
    expect=ExpectError(status=409),
)
def interactive_stale():
    """Revision conflicts are returned rather than overwriting facts."""


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
