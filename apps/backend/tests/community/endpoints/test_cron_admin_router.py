"""Registered cron admin cases through real HTTP, repositories and relay wiring."""
from agentclaw.community.core.repository.protocols.publishing import BotPublishRepositoryProtocol
from agentclaw.community.plugin_api.device_adapter_transport import DeviceAdapterTransport
from tests.community.factories.access import make_staff_user
from tests.community.factories.bot_collaborator import make_bot
from tests.community.factories.devices import make_active_local_device
from tests.community.framework import CaseInput, ExpectError, ExpectSuccess, endpoint_test

BOT_ID = "cron-admin-bot"
TASK_ID = "admin-task"
OWNER_ID = "cron-admin-owner"
LIST_PATH = "/api/cron/admin/bots/{bot_id}/tasks"
DISABLE_PATH = LIST_PATH + "/{task_id}/disable"


def _seed_published_bot(world):
    make_staff_user(world, user_id="100000")
    make_staff_user(world, user_id=OWNER_ID)
    draft = make_active_local_device(world, owner_id=OWNER_ID, device_id="admin-draft")
    published = make_active_local_device(
        world, owner_id=OWNER_ID, device_id="admin-online", adapter_port=20014,
    )
    bot = make_bot(world, bot_id=BOT_ID, owner_id=OWNER_ID, binding_id=draft)
    world.get(BotPublishRepositoryProtocol).insert({
        "source_bot_pk": bot["id"], "source_bot_id": BOT_ID,
        "publish_bot_id": "cron-admin-published", "name": "Cron admin published",
        "owner_id": OWNER_ID, "permission_owner": OWNER_ID,
        "status": "success", "version": 1, "env": "dev",
        "ext": {"binding": {"online": published}},
    })


def _seed_list(world):
    _seed_published_bot(world)
    world.get(DeviceAdapterTransport).set_response(
        "invoke", {"success": True, "data": [{"task_id": TASK_ID, "enabled": True}]},
    )


def _seed_disable(world):
    _seed_published_bot(world)
    world.get(DeviceAdapterTransport).set_response(
        "invoke", {"success": True, "data": {"task_id": TASK_ID, "enabled": False}},
    )


def _seed_denied(world):
    _seed_published_bot(world)
    # Even the Bot's owner is not implicitly a cron administrator.


def _assert_list_reached_published_adapter(response, world):
    calls = world.get(DeviceAdapterTransport).calls_to("invoke")
    assert len(calls) == 1
    assert calls[0].args[1:3] == ("GET", "/api/cron")
    assert response.json()["data"][0]["owner_id"] == OWNER_ID
    assert response.json()["data"][0]["runtime_stage"] == "online"


def _assert_disable_reached_adapter(response, world):
    calls = world.get(DeviceAdapterTransport).calls_to("invoke")
    assert len(calls) == 1
    assert calls[0].args[1:3] == ("PUT", f"/api/cron/{TASK_ID}")
    assert calls[0].args[3] == {"enabled": False}
    assert response.json()["data"]["owner_id"] == OWNER_ID
    assert response.json()["data"]["runtime_stage"] == "online"


def _assert_no_adapter_call(response, world):
    assert world.get(DeviceAdapterTransport).calls_to("invoke") == []


@endpoint_test(
    method="GET", path=LIST_PATH, scenario="cron_admin_list_online",
    input=CaseInput(headers={"x-user-id": "100000"}, path_params={"bot_id": BOT_ID},
                    query_params={"runtime_stage": "online"}),
    seed=_seed_list,
    expect=ExpectSuccess(status=200, json_contains={"success": True, "data": [{"task_id": TASK_ID}]}),
    extra_assertions=(_assert_list_reached_published_adapter,),
)
def list_online():
    """The real relay resolves the stored owner and published binding."""


@endpoint_test(
    method="GET", path=LIST_PATH, scenario="cron_admin_list_owner_denied",
    input=CaseInput(headers={"x-user-id": OWNER_ID}, path_params={"bot_id": BOT_ID},
                    query_params={"runtime_stage": "online"}),
    seed=_seed_denied,
    expect=ExpectError(status=200, json_contains={"success": False, "error_code": 403}),
    extra_assertions=(_assert_no_adapter_call,),
)
def list_denied():
    """A valid owner login without the admin scope cannot use the admin route."""


@endpoint_test(
    method="POST", path=DISABLE_PATH, scenario="cron_admin_disable_online",
    input=CaseInput(headers={"x-user-id": "100000"},
                    path_params={"bot_id": BOT_ID, "task_id": TASK_ID},
                    json_body={"runtime_stage": "online", "reason": "incident"}),
    seed=_seed_disable,
    expect=ExpectSuccess(status=200, json_contains={"success": True, "data": {"enabled": False}}),
    extra_assertions=(_assert_disable_reached_adapter,),
)
def disable_online():
    """The endpoint forwards only enabled=false, using the selected publication."""


@endpoint_test(
    method="POST", path=DISABLE_PATH, scenario="cron_admin_disable_owner_denied",
    input=CaseInput(headers={"x-user-id": OWNER_ID},
                    path_params={"bot_id": BOT_ID, "task_id": TASK_ID},
                    json_body={"runtime_stage": "online", "reason": "incident"}),
    seed=_seed_denied,
    expect=ExpectError(status=200, json_contains={"success": False, "error_code": 403}),
    extra_assertions=(_assert_no_adapter_call,),
)
def disable_denied():
    """Denied administration never invokes the runtime adapter."""
