"""Context-collab creation routes exercise the real product service and SQL UoW."""

from tests.community.endpoints.product_profile_seeds import (
    OWNER,
    PROFILE_BODY,
    seed_profile,
    seed_service,
)
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)

from agentclaw.community.core.task.context_collab.l6_products.persistence.rollout_repository import (
    SqlProductRuntimeRepository,
)
from agentclaw.community.core.task.context_collab.l6_products.product_profile_service.configuration import (
    encode_profile,
)
from agentclaw.community.plugin_api.database import DatabasePlugin

BASE = "/api/v1/collaboration/task/context_collab"
AUTH = {"x-user-id": "u1"}


def assert_profile_persisted(response, world):
    repository = SqlProductRuntimeRepository(world.get(DatabasePlugin))
    profile, owner = repository.get_profile(response.json()["data"]["id"])
    assert encode_profile(profile) == response.json()["data"]
    assert owner == OWNER.actor


def assert_collet_persisted(response, world):
    repository = SqlProductRuntimeRepository(world.get(DatabasePlugin))
    data = response.json()["data"]
    snapshot = repository.get_collet(data["id"])
    assert snapshot.collet.profile_id == data["profile_id"]
    assert snapshot.collet.content == data["content"]
    assert snapshot.collet.status.value == data["status"]
    assert snapshot.owner == OWNER.actor
    assert len(snapshot.rollouts) == 1
    assert snapshot.current().collet_id == snapshot.collet.id
    assert snapshot.current().stages[0].name == "implementation"


@endpoint_test(
    method="POST",
    path=BASE + "/product_profiles",
    scenario="happy",
    seed=seed_service,
    input=CaseInput(headers=AUTH, json_body=PROFILE_BODY),
    extra_assertions=(assert_profile_persisted,),
    expect=ExpectSuccess(
        status=200, json_contains={"data": {"name": "endpoint-profile", "id": 1}}
    ),
)
def create_profile_happy():
    """Authorized creation persists the default MultiRollout/static Swarm profile."""


@endpoint_test(
    method="POST",
    path=BASE + "/product_profiles",
    scenario="invalid_yaml",
    seed=seed_service,
    input=CaseInput(headers=AUTH, json_body={**PROFILE_BODY, "stages": "stages: []"}),
    expect=ExpectError(status=422),
)
def create_profile_invalid_yaml():
    """Actual domain YAML validation rejects an empty workflow."""


@endpoint_test(
    method="POST",
    path=BASE + "/collets",
    scenario="happy",
    seed=seed_profile,
    extra_assertions=(assert_collet_persisted,),
    input=CaseInput(headers=AUTH, json_body={"profile_id": 1, "content": "request"}),
    expect=ExpectSuccess(
        status=200,
        json_contains={"data": {"id": 1, "profile_id": 1, "status": "CREATE"}},
    ),
)
def create_collet_happy():
    """The actual SQL transaction creates the Collet with its first rollout."""


@endpoint_test(
    method="POST",
    path=BASE + "/collets",
    scenario="missing_profile",
    seed=seed_service,
    input=CaseInput(headers=AUTH, json_body={"profile_id": 999, "content": "request"}),
    expect=ExpectError(status=404),
)
def create_collet_missing_profile():
    """Missing profiles cannot produce a successful creation response."""
