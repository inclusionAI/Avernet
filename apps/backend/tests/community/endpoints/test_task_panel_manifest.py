"""Real application/DI endpoint coverage for task panel configuration."""
from agentclaw.community.api.system_config_service import SystemConfigServiceProtocol
from agentclaw.community.api.task.task_manifest_service import TaskPanelManifestServiceProtocol
from agentclaw.community.utils.env_utils import get_current_env
from tests.community.framework import CaseInput, ExpectError, ExpectSuccess, endpoint_test

PATH = "/api/v1/collaboration/tasks/manifest"
HEADERS = {"x-user-id": "manifest_operator"}
VALUE = {"schema_version": 1, "bundles": [{"name": "taskPanel", "type": "url",
    "url": "https://cdn.jsdelivr.net/npm/@avernet-assets/task-panel@0.1.1/dist/index.umd.js"}]}


def seed_manifest(world):
    world.get(TaskPanelManifestServiceProtocol).set_manifest(
        value=VALUE, env=get_current_env(), operator="manifest_operator",
    )


def assert_saved(response, world):
    assert world.get(SystemConfigServiceProtocol).get_config(
        category="task", config_key="panel", env=get_current_env(),
    ) == VALUE


@endpoint_test(method="GET", path=PATH, scenario="configured", seed=seed_manifest,
    input=CaseInput(headers=HEADERS),
    expect=ExpectSuccess(json_contains={"code": 200000, "data": {"schema_version": 1}}))
def read_configured():
    """Read the persisted descriptor through the actual application."""


@endpoint_test(method="GET", path=PATH, scenario="missing",
    input=CaseInput(headers=HEADERS), expect=ExpectError(status=503))
def read_missing():
    """Missing configuration fails closed."""


@endpoint_test(method="POST", path=PATH, scenario="create",
    input=CaseInput(headers=HEADERS, json_body=VALUE),
    expect=ExpectSuccess(json_contains={"code": 200000}), extra_assertions=(assert_saved,))
def save_valid():
    """Operator creates the category and persists the validated configuration."""


@endpoint_test(method="POST", path=PATH, scenario="invalid",
    input=CaseInput(headers=HEADERS, json_body={}), expect=ExpectError(status=422))
def save_invalid():
    """Invalid configuration cannot be saved."""
