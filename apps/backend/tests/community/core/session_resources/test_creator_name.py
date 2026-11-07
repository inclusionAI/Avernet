"""Creator names are optional snapshots of the actual authenticated actor."""
import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agentclaw.community.plugin_api.staff_dept import StaffDeptPlugin, StaffProfileInfo
from tests.community.core.session_resources.test_service import _service
from tests.community.core.session_resources.test_repository import _record


def _upload(service, **kwargs):
    return service.create_upload_intent(
        owner_id="owner-1", bot_id="bot-1", session_key="session", binding_id=42,
        scope_type="openapi_session", engine_type="openclaw", filename="a.txt", **kwargs,
    ).resource


@pytest.mark.parametrize("name", ["  花名  ", None, "   "])
def test_trusted_actor_name_snapshot(name):
    service, _, _ = _service()
    record = _upload(service, creator_name=name)
    assert record.creator_name == ("花名" if name and name.strip() else None)


@pytest.mark.parametrize("outcome", ["found", "missing", "failure", "wrong_actor"])
def test_directory_queries_actor_never_owner_and_fails_open(outcome, caplog):
    staff = Mock(spec=StaffDeptPlugin)
    service, _, _ = _service(staff_dept=staff)
    staff.get_profile_by_work_no.return_value = StaffProfileInfo(
        work_no="owner-1" if outcome == "wrong_actor" else "actor-2",
        nick_name="上传者" if outcome != "missing" else None,
    )
    if outcome == "failure":
        staff.get_profile_by_work_no.side_effect = RuntimeError("private-directory-credential")
    with caplog.at_level("INFO"):
        record = _upload(service, actor_user_id="actor-2")
    assert record.owner_id == "owner-1"
    assert record.creator_name == ("上传者" if outcome == "found" else None)
    staff.get_profile_by_work_no.assert_called_once_with(work_no="actor-2")
    assert "private-directory-credential" not in caplog.text
    assert "上传者" not in caplog.text
    assert "session_resource.creator_name" in caplog.text


def test_supplied_name_does_not_query_directory():
    staff = Mock(spec=StaffDeptPlugin)
    service, _, _ = _service(staff_dept=staff)
    assert _upload(service, actor_user_id="actor-2", creator_name="上传者").creator_name == "上传者"
    staff.get_profile_by_work_no.assert_not_called()


def test_no_actor_does_not_lookup_owner():
    staff = Mock(spec=StaffDeptPlugin)
    service, _, _ = _service(staff_dept=staff)
    assert _upload(service).creator_name is None
    staff.get_profile_by_work_no.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("principal_id,name,expected", [
    ("actor-2", " 花名 ", "花名"),
    ("actor-2", " ", None),
    ("actor-2", None, None),
    ("owner-1", "Owner名", None),
])
async def test_principal_name_only_belongs_to_exact_actor(principal_id, name, expected):
    from agentclaw.community.adapters.http.openapi_v1.engine_runtime.sessions.dependencies_session_files import upload_actor_name
    principal = SimpleNamespace(user=SimpleNamespace(id=principal_id, display_name=name))
    assert await upload_actor_name("actor-2", principal) == expected


@pytest.mark.asyncio
async def test_application_only_principal_has_no_name():
    from agentclaw.community.adapters.http.openapi_v1.engine_runtime.sessions.dependencies_session_files import upload_actor_name
    assert await upload_actor_name("actor-2", SimpleNamespace(user=None)) is None


def test_openapi_adapter_passes_actual_actor_to_directory_and_preserves_snapshot():
    from agentclaw.community.adapters.http.openapi_v1.engine_runtime.sessions.dependencies_session_files import OpenApiSessionFileAdapter
    staff = Mock(spec=StaffDeptPlugin)
    service, repo, _ = _service(staff_dept=staff)
    staff.get_profile_by_work_no.return_value = StaffProfileInfo("actor-2", "上传者")
    bindings = Mock()
    bindings.resolve.return_value = SimpleNamespace(binding_id=42)
    adapter = OpenApiSessionFileAdapter(service, bindings, Mock(), Mock())
    intents = adapter.create_upload_intents(
        actor_user_id="actor-2", owner_id="owner-1", bot_id="bot-1",
        session_key="session", stage="draft", engine_type="openclaw",
        files=[("a.txt", 1, None)],
    )
    record = intents[0].resource
    assert record.creator_name == "上传者"
    assert record.owner_id == "owner-1"
    assert bindings.resolve.call_args.args[0].actor_user_id == "actor-2"
    staff.get_profile_by_work_no.assert_called_once_with(work_no="actor-2")
    # Later completion/status must keep the first name without consulting a changed directory.
    staff.get_profile_by_work_no.return_value = StaffProfileInfo("actor-2", "新花名")
    completed = service.complete_upload(
        owner_id=record.owner_id, bot_id=record.bot_id, session_key="session",
        resource_id=record.resource_id, transfer_id=record.transfer_id,
    )
    assert completed.creator_name == "上传者"
    assert repo.value.creator_name == "上传者"
    staff.get_profile_by_work_no.assert_called_once()


@pytest.mark.parametrize("name", ["花名", None])
def test_record_helper_and_openapi_schema_include_nullable_name(name):
    from agentclaw.community.adapters.http.openapi_v1.engine_runtime.sessions.schemas import SessionFile
    from agentclaw.community.adapters.http.openapi_v1.engine_runtime.sessions.schemas_helpers import _session_file_resource
    from agentclaw.community.adapters.http.session_resources.router import _resource

    record = replace(_record(), creator_name=name)
    assert _session_file_resource(record).model_dump()["creator_name"] == name
    assert _resource(record)["creator_name"] == name
    schema = SessionFile.model_json_schema()
    assert "creator_name" not in schema.get("required", [])
    assert {"type": "null"} in schema["properties"]["creator_name"]["anyOf"]


def test_missing_optional_directory_keeps_unknown_actor_name_null():
    service, _, _ = _service()
    assert _upload(service, actor_user_id="actor-2").creator_name is None


@pytest.mark.parametrize("name,expected", [
    (" 名字 ", "名字"), ("名" * 255, "名" * 255),
    ("名" * 256, None), ("花\n名", None), ("花\t名", None), ("花\x00名", None),
])
@pytest.mark.parametrize("source", ["principal", "directory", "legacy"])
def test_invalid_metadata_never_prevents_upload_or_materialization(name, expected, source):
    from agentclaw.community.adapters.http.openapi_v1.engine_runtime.sessions.dependencies_session_files import upload_actor_name
    from agentclaw.community.core.session_resources.materialization import SessionResourceMaterializeHandler
    from agentclaw.community.core.task_queue.types import Complete
    from tests.community.core.session_resources.test_materialization import _Transport, _Resolver

    staff = Mock(spec=StaffDeptPlugin)
    staff.get_profile_by_work_no.return_value = StaffProfileInfo("actor-2", name)
    service, repository, _ = _service(staff_dept=staff)
    kwargs = {}
    if source == "directory":
        kwargs["actor_user_id"] = "actor-2"
    elif source == "principal":
        principal = SimpleNamespace(user=SimpleNamespace(id="actor-2", display_name=name))
        kwargs["creator_name"] = asyncio.run(upload_actor_name("actor-2", principal))
    else:
        kwargs["creator_name"] = name
    record = _upload(service, **kwargs)
    assert record.creator_name == expected
    started = service.complete_upload(
        owner_id=record.owner_id, bot_id=record.bot_id, session_key="session",
        resource_id=record.resource_id, transfer_id=record.transfer_id,
    )
    transport = _Transport()
    handler = SessionResourceMaterializeHandler(_Resolver(), transport, repository, service._vault)
    outcome = handler.handle({
        "resource_id": started.resource_id, "task_id": started.task_id,
        "task_version": started.task_version,
    })
    assert isinstance(outcome, Complete)
    assert transport.calls[0][3]["creator_name"] == expected
