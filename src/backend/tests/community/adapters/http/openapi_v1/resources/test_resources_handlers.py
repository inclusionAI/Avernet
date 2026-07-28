"""openapi_v1 resources handler unit tests: mapping + handler behavior."""

from datetime import datetime
from types import SimpleNamespace
from typing import List

import pytest
from fastapi import HTTPException, Response

from agentclaw.community.adapters.http.openapi_v1.contracts import (
    CODE_CREATED,
    CODE_OK,
    Deleted,
    Envelope,
    NameCheck,
    Page,
    PageParams,
)
from agentclaw.community.adapters.http.openapi_v1.resources.router import (
    _to_openapi_resource,
    check_resource_name,
    create_resource,
    delete_resource,
    download_resource,
    get_resource,
    list_resources,
    preview_resource,
    update_resource,
    upload_resource,
)
from agentclaw.community.adapters.http.openapi_v1.resources.schemas import (
    Preview,
    ResourceCreate,
    ResourceUpdate,
    ResourceType as OpenapiType,
)
from agentclaw.community.core.resources.factory import ResourceServiceFactory
from agentclaw.community.core.resources.models import (
    Resource,
    ResourceType as LegacyType,
    create_link_resource,
)


def _legacy(**ov) -> Resource:
    base = dict(
        id=1,
        name="r",
        resource_type=LegacyType.LINK,
        attributes={"url": "https://example.com", "link_type": "external"},
        user_id="u",
        created_by="c",
        source="yuque",
        bolt_id="bot-a",
    )
    base.update(ov)
    return Resource(**base)


def test_to_openapi_resource_maps_basic_fields():
    o = _to_openapi_resource(_legacy())
    assert o.resource_id == "1"
    assert o.name == "r"
    assert o.source == "yuque"
    assert o.type == OpenapiType.LINK


def test_to_openapi_resource_url_flattened():
    assert _to_openapi_resource(_legacy()).url == "https://example.com"


def test_to_openapi_resource_file_has_size():
    r = _legacy(resource_type=LegacyType.FILE, attributes={"path": "/p", "size": 42})
    o = _to_openapi_resource(r)
    assert o.type == OpenapiType.FILE
    assert o.size == 42


def test_to_openapi_resource_iso_timestamps():
    ts = datetime(2026, 7, 28, 10, 0)
    o = _to_openapi_resource(_legacy(gmt_created=ts, gmt_modified=ts))
    assert o.gmt_create == ts.isoformat()
    assert o.gmt_modified == ts.isoformat()


def test_request_id_returns_nonempty_string_in_request_context():
    """_request_id() returns a string (possibly empty outside a request;
    integration via X-Trace-Id header is tested at the handler level)."""
    from agentclaw.community.adapters.http.openapi_v1.resources.router import (
        _request_id,
    )

    val = _request_id()
    assert isinstance(val, str)


# ── list_resources handler wiring (Phase 1 Task 2) ──────────────────────
#
# Direct handler invocation (退路 B per task spec): bypasses FastAPI's
# dependency wiring and supplies a stub factory. `principal` is `Any`, so
# `None` is an acceptable stand-in. `request=None` exercises the
# "outside-a-request" branch of `_request_id_from`.


class _StubService:
    """Minimal stub satisfying the ResourceServiceProtocol list_resources seam."""

    def __init__(self, items: List[Resource]) -> None:
        self._items = items
        self.last_call_kwargs: dict = {}

    def list_resources(self, *args, **kwargs):
        self.last_call_kwargs = dict(kwargs)
        return self._items

    def get_resource(self, resource_id):
        # Sync lookup (matches concrete ResourceService.get_resource).
        # Captures the call kwargs so tests can assert what was passed.
        self.last_call_kwargs = dict(resource_id=resource_id)
        for r in self._items:
            if str(r.id) == str(resource_id):
                return r
        return None

    async def check_name_exists(
        self,
        *,
        name: str,
        resource_type,
        parent_path=None,
        user_id=None,
        exclude_id=None,
    ):
        self.last_call_kwargs = dict(
            name=name,
            resource_type=resource_type,
            parent_path=parent_path,
            user_id=user_id,
            exclude_id=exclude_id,
        )
        # Names equal to "taken" are considered to already exist; everything
        # else is available. This lets one stub satisfy both exists-branches.
        return name == "taken"

    async def create_url_resource(
        self,
        *,
        name: str,
        url: str,
        method: str = "GET",
        headers=None,
        parent_path=None,
        user_id=None,
        created_by=None,
    ):
        """Mirrors the real service seam: returns a LINK Resource for new names,
        raises ValueError for the reserved "taken" name (duplicate → 409 at the
        handler)."""
        self.last_call_kwargs = dict(
            name=name, url=url, method=method, parent_path=parent_path,
            user_id=user_id, created_by=created_by,
        )
        if name == "taken":
            raise ValueError(f"Resource '{name}' already exists")
        # Return a LINK Resource with the url in attributes (matches how the
        # real create_url_resource populates it; _to_openapi_resource flattens
        # url/size out of attributes).
        return Resource(
            id=1,
            name=name,
            resource_type=LegacyType.LINK,
            attributes={"url": url, "link_type": "external"},
            user_id=user_id,
            source=created_by,
        )

    async def update_link_resource(
        self,
        *,
        resource_id: str,
        link_type=None,
        url=None,
        name=None,
    ):
        """Mirrors the real service seam: raises ValueError for not-found /
        URL conflict → 409 at the handler; otherwise returns a LINK Resource
        reflecting the requested rename/url change."""
        self.last_call_kwargs = dict(
            resource_id=resource_id, link_type=link_type, url=url, name=name,
        )
        # resource_id == "0" simulates a missing record (service raises
        # ValueError → 409 Conflict per legacy + create parity).
        if str(resource_id) == "0":
            raise ValueError(f"LINK resource '{resource_id}' not found")
        # url == "conflict" simulates a URL uniqueness violation.
        if url == "conflict":
            raise ValueError("URL already exists")
        return Resource(
            id=int(resource_id),
            name=name or "r",
            resource_type=LegacyType.LINK,
            attributes={"url": url or "https://x.com", "link_type": "external"},
        )

    async def delete_resource(
        self,
        resource_id: str,
        *,
        device_fs=None,
    ) -> bool:
        """Mirror of the concrete ResourceService.delete_resource seam.

        ASYNC now (Phase 3 slim service awaits device_fs.delete_file for
        file resources) — the handler must ``await`` it. Returns True for
        any id except ``"0"``, which simulates a not-found record → the
        handler maps False → 404.
        """
        self.last_call_kwargs = dict(resource_id=resource_id)
        self.last_call_device_fs = device_fs
        return str(resource_id) != "0"

    async def upload_file(
        self,
        *,
        data: bytes,
        filename: str,
        parent_path: str = "",
        user_id=None,
        device_fs=None,
    ) -> Resource:
        """Mirror of the concrete ResourceService.upload_file seam.

        ASYNC — the handler must ``await`` it (parallel to delete_resource).
        Raises ValueError on the reserved "taken" filename to exercise the
        duplicate-name → 409 path; otherwise returns a FILE Resource with
        path/size attributes that ``_to_openapi_resource`` flattens.
        """
        self.last_call_kwargs = dict(
            data=data, filename=filename, parent_path=parent_path,
            user_id=user_id,
        )
        self.last_call_device_fs = device_fs
        if filename == "taken":
            raise ValueError(f"Resource '{filename}' already exists")
        return Resource(
            id=1,
            name=filename,
            resource_type=LegacyType.FILE,
            attributes={"path": f"/{filename}", "size": len(data)},
        )

    async def download_resource(
        self,
        resource_id: str,
        *,
        device_fs=None,
    ) -> tuple[bytes, str] | None:
        """Mirror of the concrete ResourceService.download_resource seam.

        ASYNC — the handler must ``await`` it (parallel to upload_file /
        unlike sync delete_resource). Returns ``None`` for ``resource_id
        == "0"`` to exercise the not-found / not-a-file / unreadable
        collapse-to-404 path; otherwise returns a fixed ``(bytes, mime)``
        pair. The stub does NOT call ``device_fs.read_file`` — handler
        wiring (device_fs forward + 404 mapping) is what's under test here,
        not the device_fs read path.
        """
        self.last_call_kwargs = dict(resource_id=resource_id)
        self.last_call_device_fs = device_fs
        if str(resource_id) == "0":
            return None
        return (b"file content", "application/pdf")

    async def preview_resource(
        self,
        resource_id: str,
        *,
        device_fs=None,
        max_size: int = 1_048_576,
    ) -> dict | None:
        """Mirror of the concrete ResourceService.preview_resource seam.

        ASYNC — the handler must ``await`` it (parallel to upload_file /
        download_resource). Returns a fixed ``{content, content_type,
        size}`` dict for the happy path; returns ``None`` for
        ``resource_id == "0"`` (not-found / not-a-file / directory /
        unreadable / empty collapse-to-404); raises ``ValueError`` for
        ``resource_id == "too-large"`` to exercise the >1 MB cap → 413
        path. Like the download stub, it does NOT call
        ``device_fs.read_file`` — handler wiring (device_fs forward,
        ValueError → 413, None → 404) is what's under test here.
        """
        self.last_call_kwargs = dict(resource_id=resource_id, max_size=max_size)
        self.last_call_device_fs = device_fs
        if str(resource_id) == "0":
            return None
        if str(resource_id) == "too-large":
            raise ValueError(
                f"File too large for preview (max {max_size} bytes)"
            )
        return {"content": "preview body", "content_type": "text/plain", "size": 12}


class _StubFactory:
    """Captures bot_id passed to create(); returns the configured service."""

    def __init__(self, service: _StubService) -> None:
        self._service = service
        self.created_bot_ids: list[str] = []

    def create(self, *, bot_id: str) -> _StubService:
        self.created_bot_ids.append(bot_id)
        return self._service


@pytest.mark.asyncio
async def test_list_resources_returns_envelope_with_page():
    service = _StubService([_legacy(id=1, name="r1"), _legacy(id=2, name="r2")])
    factory = _StubFactory(service)

    env = await list_resources(
        page=PageParams(page=1, page_size=20),
        principal=None,
        bot_id="bot-a",
        type=None,
        factory=factory,
        request=None,
    )

    assert isinstance(env, Envelope)
    assert env.code == CODE_OK
    assert env.message == "OK"
    assert env.data is not None
    assert isinstance(env.data, Page)
    assert env.data.total == 2
    assert len(env.data.items) == 2
    assert env.data.items[0].resource_id == "1"
    assert env.data.items[1].resource_id == "2"
    # mapping: legacy LINK → openapi LINK, source/url flattened
    assert env.data.items[0].type == OpenapiType.LINK
    assert env.data.items[0].url == "https://example.com"


@pytest.mark.asyncio
async def test_list_resources_paginates_items():
    service = _StubService(
        [
            _legacy(id=1, name="r1"),
            _legacy(id=2, name="r2"),
            _legacy(id=3, name="r3"),
        ]
    )
    factory = _StubFactory(service)

    env = await list_resources(
        page=PageParams(page=2, page_size=1),
        principal=None,
        bot_id="bot-a",
        type=None,
        factory=factory,
        request=None,
    )

    # total reflects the full list; current page slice holds the 2nd item only
    assert env.data.total == 3
    assert [item.resource_id for item in env.data.items] == ["2"]


@pytest.mark.asyncio
async def test_list_resources_passes_type_filter_value_to_service():
    service = _StubService([])
    factory = _StubFactory(service)

    await list_resources(
        page=PageParams(),
        principal=None,
        bot_id="bot-a",
        type=OpenapiType.LINK,
        factory=factory,
        request=None,
    )

    # Fix #1: the openapi enum is mapped to the legacy ResourceType enum at
    # the handler seam (the slim service does ``.value`` internally — passing
    # the openapi enum's ``.value`` string broke at filter time). ``LegacyType``
    # subclasses ``str``, so this assertion still passes via str equality, but
    # the stricter ``is`` check lives in ``test_list_resources_passes_legacy_enum_to_service``.
    assert service.last_call_kwargs.get("resource_type") == "link"


@pytest.mark.asyncio
async def test_list_resources_reads_x_trace_id_from_request():
    factory = _StubFactory(_StubService([]))
    request = SimpleNamespace(headers={"x-trace-id": "trace-abc"})

    env = await list_resources(
        page=PageParams(),
        principal=None,
        bot_id="bot-a",
        type=None,
        factory=factory,
        request=request,
    )

    assert env.request_id == "trace-abc"


@pytest.mark.asyncio
async def test_list_resources_request_id_empty_when_no_request_context():
    factory = _StubFactory(_StubService([]))

    env = await list_resources(
        page=PageParams(),
        principal=None,
        bot_id="bot-a",
        type=None,
        factory=factory,
        request=None,
    )

    assert env.request_id == ""


@pytest.mark.asyncio
async def test_list_resources_empty_result_returns_empty_page():
    factory = _StubFactory(_StubService([]))

    env = await list_resources(
        page=PageParams(),
        principal=None,
        bot_id="bot-a",
        type=None,
        factory=factory,
        request=None,
    )

    assert env.data.total == 0
    assert env.data.items == []


# ── check_resource_name handler wiring (Phase 1 Task 3) ──────────────────
#
# Same direct-handler-invocation pattern as Task 2: bypass FastAPI DI,
# supply a stub factory. `_StubService.check_name_exists` returns True iff
# `name == "taken"`, exercising both exists-branches with one stub.


@pytest.mark.asyncio
async def test_check_name_returns_envelope_with_exists_false_for_available():
    service = _StubService([])
    factory = _StubFactory(service)

    env = await check_resource_name(
        name="available",
        principal=None,
        type=None,
        bot_id="bot-x",
        factory=factory,
        request=None,
    )

    assert isinstance(env, Envelope)
    assert env.code == CODE_OK
    assert env.message == "OK"
    assert env.data is not None
    assert isinstance(env.data, NameCheck)
    assert env.data.name == "available"
    assert env.data.exists is False


@pytest.mark.asyncio
async def test_check_name_returns_exists_true_for_taken():
    service = _StubService([])
    factory = _StubFactory(service)

    env = await check_resource_name(
        name="taken",
        principal=None,
        type=None,
        bot_id=None,
        factory=factory,
        request=None,
    )

    assert env.data.exists is True
    assert env.data.name == "taken"


@pytest.mark.asyncio
async def test_check_name_reads_x_trace_id_from_request():
    factory = _StubFactory(_StubService([]))
    request = SimpleNamespace(headers={"x-trace-id": "trace-xyz"})

    env = await check_resource_name(
        name="available",
        principal=None,
        type=None,
        bot_id="bot-a",
        factory=factory,
        request=request,
    )

    assert env.request_id == "trace-xyz"


@pytest.mark.asyncio
async def test_check_name_passes_type_value_to_service_when_provided():
    service = _StubService([])
    factory = _StubFactory(service)

    await check_resource_name(
        name="available",
        principal=None,
        type=OpenapiType.FILE,
        bot_id="bot-a",
        factory=factory,
        request=None,
    )

    # Fix #1: passed value is the legacy ResourceType enum (str-subclass —
    # equality with "file" still holds). parent_path/user_id are explicitly
    # forwarded as None (slim service requires them as kwarg, no defaults).
    assert service.last_call_kwargs.get("resource_type") == "file"
    assert service.last_call_kwargs.get("parent_path") is None
    assert service.last_call_kwargs.get("user_id") is None


# ── get_resource handler wiring (Phase 1 Task 4) ─────────────────────────
#
# Same stub-factory pattern as Tasks 2/3. Note: `get_resource` on the
# concrete service is SYNC (unlike `check_name_exists` which is async);
# the handler therefore must NOT `await` it. `_StubService.get_resource`
# mirrors that contract.


@pytest.mark.asyncio
async def test_get_resource_returns_envelope_when_found():
    service = _StubService([_legacy(id=1, name="r")])
    factory = _StubFactory(service)

    env = await get_resource(
        resource_id="1",
        principal=None,
        bot_id="bot-x",
        factory=factory,
        request=None,
    )

    assert isinstance(env, Envelope)
    assert env.code == CODE_OK
    assert env.message == "OK"
    assert env.data is not None
    assert env.data.resource_id == "1"
    assert env.data.name == "r"
    assert env.data.type == OpenapiType.LINK
    assert env.data.url == "https://example.com"
    assert env.request_id == ""  # no request context


@pytest.mark.asyncio
async def test_get_resource_raises_404_when_missing():
    factory = _StubFactory(_StubService([]))

    with pytest.raises(HTTPException) as exc:
        await get_resource(
            resource_id="999",
            principal=None,
            bot_id=None,
            factory=factory,
            request=None,
        )

    assert exc.value.status_code == 404
    assert exc.value.detail == "Resource not found"


@pytest.mark.asyncio
async def test_get_resource_reads_x_trace_id_from_request():
    factory = _StubFactory(_StubService([_legacy(id=1)]))
    request = SimpleNamespace(headers={"x-trace-id": "trace-get-1"})

    env = await get_resource(
        resource_id="1",
        principal=None,
        bot_id="bot-a",
        factory=factory,
        request=request,
    )

    assert env.request_id == "trace-get-1"


# ── create_resource (Phase 1, LINK-only) ───────────────────────────


@pytest.mark.asyncio
async def test_create_link_returns_201_envelope():
    service = _StubService([])
    factory = _StubFactory(service)
    body = ResourceCreate(name="mylink", type=OpenapiType.LINK, url="https://x.com")

    env = await create_resource(
        body=body, principal=None, bot_id="bot-x", factory=factory, request=None,
    )

    assert env.code == CODE_CREATED
    assert env.data.resource_id == "1"
    assert env.data.type == OpenapiType.LINK
    assert env.data.url == "https://x.com"
    # bot_id flowed through to factory.create
    assert factory.created_bot_ids == ["bot-x"]


@pytest.mark.asyncio
async def test_create_file_points_to_upload_with_400():
    service = _StubService([])
    factory = _StubFactory(service)
    body = ResourceCreate(name="f", type=OpenapiType.FILE)

    with pytest.raises(HTTPException) as exc:
        await create_resource(
            body=body, principal=None, bot_id=None, factory=factory, request=None,
        )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_create_folder_is_not_yet_supported_501():
    service = _StubService([])
    factory = _StubFactory(service)
    body = ResourceCreate(name="d", type=OpenapiType.FOLDER)

    with pytest.raises(HTTPException) as exc:
        await create_resource(
            body=body, principal=None, bot_id=None, factory=factory, request=None,
        )
    assert exc.value.status_code == 501


@pytest.mark.asyncio
async def test_create_link_without_url_is_400():
    service = _StubService([])
    factory = _StubFactory(service)
    body = ResourceCreate(name="link", type=OpenapiType.LINK, url=None)

    with pytest.raises(HTTPException) as exc:
        await create_resource(
            body=body, principal=None, bot_id=None, factory=factory, request=None,
        )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_create_link_duplicate_name_is_409():
    service = _StubService([])  # create_url_resource raises ValueError for name=="taken"
    factory = _StubFactory(service)
    body = ResourceCreate(name="taken", type=OpenapiType.LINK, url="https://x.com")

    with pytest.raises(HTTPException) as exc:
        await create_resource(
            body=body, principal=None, bot_id=None, factory=factory, request=None,
        )
    assert exc.value.status_code == 409


# ── update_resource (Phase 3 Task 1, LINK-only) ─────────────────────
#
# Same direct-handler-invocation pattern as Phase 1 handlers: bypass
# FastAPI DI, supply a stub factory. `_StubService.update_link_resource`
# raises ValueError for not-found / URL conflict → 409 Conflict (legacy +
# create parity). link_type is intentionally not exposed on the openapi
# ResourceUpdate contract; the service still accepts it but the handler
# never forwards it.


@pytest.mark.asyncio
async def test_update_link_returns_200_envelope():
    service = _StubService([])
    factory = _StubFactory(service)
    body = ResourceUpdate(name="renamed", url="https://new.com")

    env = await update_resource(
        resource_id="1", body=body, principal=None,
        bot_id="bot-x", factory=factory, request=None,
    )

    assert env.code == CODE_OK
    assert env.message == "OK"
    assert env.data is not None
    assert env.data.resource_id == "1"
    assert env.data.name == "renamed"
    assert env.data.type == OpenapiType.LINK
    assert env.data.url == "https://new.com"
    # bot_id flowed through to factory.create
    assert factory.created_bot_ids == ["bot-x"]
    # service received the rename + url, not link_type (contract doesn't expose it)
    assert service.last_call_kwargs.get("name") == "renamed"
    assert service.last_call_kwargs.get("url") == "https://new.com"


@pytest.mark.asyncio
async def test_update_link_raises_409_when_not_found():
    service = _StubService([])  # resource_id="0" → not-found ValueError
    factory = _StubFactory(service)
    body = ResourceUpdate(name="x")

    with pytest.raises(HTTPException) as exc:
        await update_resource(
            resource_id="0", body=body, principal=None,
            bot_id=None, factory=factory, request=None,
        )
    assert exc.value.status_code == 409
    # legacy "not found" message surfaces through ValueError → detail
    assert "not found" in exc.value.detail


@pytest.mark.asyncio
async def test_update_link_raises_409_on_url_conflict():
    service = _StubService([])
    factory = _StubFactory(service)
    body = ResourceUpdate(url="conflict")

    with pytest.raises(HTTPException) as exc:
        await update_resource(
            resource_id="1", body=body, principal=None,
            bot_id=None, factory=factory, request=None,
        )
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_update_link_reads_x_trace_id_from_request():
    factory = _StubFactory(_StubService([]))
    request = SimpleNamespace(headers={"x-trace-id": "trace-upd-1"})
    body = ResourceUpdate(name="renamed")

    env = await update_resource(
        resource_id="1", body=body, principal=None,
        bot_id="bot-a", factory=factory, request=request,
    )

    assert env.request_id == "trace-upd-1"


# ── delete_resource (Phase 3 Task 2 — first real device_fs handler) ────
#
# The device_fs resolution chain (bot_repo → get_device_info → resolver →
# dispatcher → device_fs) is stubbed per-dependency to keep this a handler
# unit test, not an integration test. owner_id is read off the bot record
# (bot_repo.get_by_id) — there is no ctx.user_id yet (Direction A will wire
# principal → user_id). delete_resource on the concrete service is SYNC;
# the handler must NOT `await` it. DeviceNotBoundError from resolver is out
# of scope here — the stub resolver never raises.


class _StubBotRepo:
    """Two-method stub feeding the handler + get_device_info.

    get_by_id returns the bot record dict (with ``owner_id``) or None;
    get_device_provider_by_bot_id_and_owner returns the device binding
    tuple (``{device_provider, sandbox_id}``) that get_device_info reads.
    """

    def __init__(self, bot_dict=None, device_info=None):
        self._bot = bot_dict
        self._device_info = device_info

    def get_by_id(self, bot_id):
        return self._bot

    def get_device_provider_by_bot_id_and_owner(self, bot_id, owner_id):
        return self._device_info


class _StubResolver:
    """resolve_for_bot returns a minimal ctx object the dispatcher accepts."""

    def resolve_for_bot(self, bot_id, user_id, *, device_uuid=None):
        return SimpleNamespace(provider="arca", device_id="dev-1")


class _StubDispatcher:
    """dispatch returns the configured device_fs regardless of ctx."""

    def __init__(self, device_fs):
        self._fs = device_fs

    def dispatch(self, ctx):
        return self._fs


class _StubDeviceFs:
    """Records paths handed to delete_file / write_file / read_file; no real I/O."""

    def __init__(self):
        self.deleted_paths: list[str] = []
        self.read_paths: list[str] = []
        self.written: list[tuple[str, bytes]] = []
        self._files: dict[str, bytes] = {}

    async def delete_file(self, path):
        self.deleted_paths.append(path)
        self._files.pop(path, None)

    async def write_file(self, path, data):
        """Stub for the upload write seam. Records the write and stores the
        bytes so a subsequent read_file round-trips them (integration tests
        that exercise the real slim service's upload→download path get the
        same bytes back, not a fixed payload)."""
        self.written.append((path, data))
        self._files[path] = data

    async def read_file(self, file_path, *, enforce_download_limit=False):
        """Stub for the preview / download read seam.

        Records the path and returns the stored payload (from a prior
        write_file) so integration tests get a real round-trip; falls back
        to a fixed non-empty payload for stub-service tests that never
        write first. Returns ``b""`` for paths ending in ``"missing"`` to
        exercise the empty-content branch of the concrete service (legacy
        parity: empty → 404, not an empty preview body).
        """
        self.read_paths.append(file_path)
        if file_path in self._files:
            return self._files[file_path]
        if file_path.endswith("missing"):
            return b""
        return b"device-fs bytes"


_DEFAULT_BOT = {"owner_id": "own-a", "bot_id": "b"}
_DEFAULT_DEVICE_INFO = {"device_provider": "arca", "sandbox_id": "sb-1"}


def _delete_deps(*, bot_dict=_DEFAULT_BOT, device_info=None, service=None):
    """Bundle the four device_fs deps + a stub factory in one call site.

    Returns ``(factory, bot_repo, resolver, dispatcher, device_fs)`` so each
    test stays focused on the assertion, not the wiring. Pass
    ``bot_dict=None`` to exercise the bot-not-found 404 path — the default
    sentinel distinguishes "absent" from "explicitly None".
    """
    device_fs = _StubDeviceFs()
    return (
        _StubFactory(service or _StubService([])),
        _StubBotRepo(
            bot_dict=bot_dict,
            device_info=device_info or _DEFAULT_DEVICE_INFO,
        ),
        _StubResolver(),
        _StubDispatcher(device_fs),
        device_fs,
    )


@pytest.mark.asyncio
async def test_delete_returns_200_envelope_with_deleted_true():
    service = _StubService([])
    factory, bot_repo, resolver, dispatcher, device_fs = _delete_deps(service=service)

    env = await delete_resource(
        resource_id="1", principal=None,
        bot_id="bot-x", factory=factory,
        bot_repo=bot_repo, resolver=resolver,
        device_fs_dispatcher=dispatcher, request=None,
    )

    assert isinstance(env, Envelope)
    assert env.code == CODE_OK
    assert env.message == "OK"
    assert env.data is not None
    assert isinstance(env.data, Deleted)
    assert env.data.deleted is True
    # device_fs resolved by the dispatcher was forwarded to the service
    assert service.last_call_device_fs is device_fs
    # service received the resource_id (no device_provider/sandbox_id on
    # the slim contract — device_fs is the sole device boundary)
    assert service.last_call_kwargs.get("resource_id") == "1"
    # bot_id flowed through to factory.create
    assert factory.created_bot_ids == ["bot-x"]


@pytest.mark.asyncio
async def test_delete_raises_404_when_bot_missing():
    factory, bot_repo, resolver, dispatcher, _ = _delete_deps(bot_dict=None)

    with pytest.raises(HTTPException) as exc:
        await delete_resource(
            resource_id="1", principal=None,
            bot_id="ghost", factory=factory,
            bot_repo=bot_repo, resolver=resolver,
            device_fs_dispatcher=dispatcher, request=None,
        )

    assert exc.value.status_code == 404
    assert "Bot" in exc.value.detail
    assert "ghost" in exc.value.detail


@pytest.mark.asyncio
async def test_delete_raises_404_when_resource_missing():
    # service.delete_resource returns False for resource_id == "0"
    service = _StubService([])
    factory, bot_repo, resolver, dispatcher, _ = _delete_deps(service=service)

    with pytest.raises(HTTPException) as exc:
        await delete_resource(
            resource_id="0", principal=None,
            bot_id="bot-a", factory=factory,
            bot_repo=bot_repo, resolver=resolver,
            device_fs_dispatcher=dispatcher, request=None,
        )

    assert exc.value.status_code == 404
    assert exc.value.detail == "Resource not found"


@pytest.mark.asyncio
async def test_delete_reads_x_trace_id_from_request():
    factory, bot_repo, resolver, dispatcher, _ = _delete_deps()
    request = SimpleNamespace(headers={"x-trace-id": "trace-del-1"})

    env = await delete_resource(
        resource_id="1", principal=None,
        bot_id="bot-a", factory=factory,
        bot_repo=bot_repo, resolver=resolver,
        device_fs_dispatcher=dispatcher, request=request,
    )

    assert env.request_id == "trace-del-1"


# ── upload_resource (Phase 3 Task 3 — second real device_fs handler) ──
#
# Same device_fs resolution chain as Task 2 delete (bot_repo → owner_id →
# resolver.resolve_for_bot → dispatcher.dispatch → device_fs), but upload
# does NOT call get_device_info (that's a delete-only concern for picking
# arca/local). upload_file on the concrete service is ASYNC — the handler
# must ``await`` it. ValueError (duplicate name) → 409 Conflict (legacy +
# create parity).
#
# Reuses the ``_delete_deps`` helper — the 4 device_fs stubs it bundles
# (factory/bot_repo/resolver/dispatcher/device_fs) are device_fs-wiring-
# generic, not delete-specific. The name is a Task 2 artifact; renaming
# would churn Task 2 tests without value.


@pytest.mark.asyncio
async def test_upload_returns_201_envelope_and_threads_device_fs():
    service = _StubService([])
    factory, bot_repo, resolver, dispatcher, device_fs = _delete_deps(service=service)

    env = await upload_resource(
        name="hello.txt", content=b"file bytes",
        principal=None, bot_id="bot-x", factory=factory,
        bot_repo=bot_repo, resolver=resolver,
        device_fs_dispatcher=dispatcher, request=None,
    )

    assert isinstance(env, Envelope)
    assert env.code == CODE_CREATED
    assert env.message == "Created"
    assert env.data is not None
    assert env.data.resource_id == "1"
    assert env.data.name == "hello.txt"
    assert env.data.type == OpenapiType.FILE
    assert env.data.size == len(b"file bytes")
    # device_fs resolved by the dispatcher was forwarded to the service
    assert service.last_call_device_fs is device_fs
    # upload_file received the file bytes + filename
    assert service.last_call_kwargs.get("data") == b"file bytes"
    assert service.last_call_kwargs.get("filename") == "hello.txt"
    # owner_id from bot record → user_id (slim upload_file has no
    # created_by param — created_by is omitted from the slim contract)
    assert service.last_call_kwargs.get("user_id") == "own-a"
    # bot_id flowed through to factory.create
    assert factory.created_bot_ids == ["bot-x"]


@pytest.mark.asyncio
async def test_upload_raises_404_when_bot_missing():
    factory, bot_repo, resolver, dispatcher, _ = _delete_deps(bot_dict=None)

    with pytest.raises(HTTPException) as exc:
        await upload_resource(
            name="hello.txt", content=b"x",
            principal=None, bot_id="ghost", factory=factory,
            bot_repo=bot_repo, resolver=resolver,
            device_fs_dispatcher=dispatcher, request=None,
        )

    assert exc.value.status_code == 404
    assert "Bot" in exc.value.detail
    assert "ghost" in exc.value.detail


@pytest.mark.asyncio
async def test_upload_raises_409_on_duplicate_name():
    # upload_file raises ValueError for filename == "taken"
    service = _StubService([])
    factory, bot_repo, resolver, dispatcher, _ = _delete_deps(service=service)

    with pytest.raises(HTTPException) as exc:
        await upload_resource(
            name="taken", content=b"x",
            principal=None, bot_id="bot-a", factory=factory,
            bot_repo=bot_repo, resolver=resolver,
            device_fs_dispatcher=dispatcher, request=None,
        )

    assert exc.value.status_code == 409
    assert "already exists" in exc.value.detail


@pytest.mark.asyncio
async def test_upload_reads_x_trace_id_from_request():
    factory, bot_repo, resolver, dispatcher, _ = _delete_deps()
    request = SimpleNamespace(headers={"x-trace-id": "trace-up-1"})

    env = await upload_resource(
        name="hello.txt", content=b"x",
        principal=None, bot_id="bot-a", factory=factory,
        bot_repo=bot_repo, resolver=resolver,
        device_fs_dispatcher=dispatcher, request=request,
    )

    assert env.request_id == "trace-up-1"


# ── download_resource (Phase 3 Task 4 — third real device_fs handler) ─
#
# Same device_fs resolution chain as Task 2 delete / Task 3 upload
# (bot_repo → owner_id → resolver.resolve_for_bot → dispatcher.dispatch →
# device_fs). Like upload, download does NOT call get_device_info (that's
# a delete-only concern for picking arca/local). download_resource on the
# concrete service is ASYNC — the handler must ``await`` it. Service
# returns (bytes, mime) or None → 404 (not-found / not-a-file /
# is-directory / read-failure all collapse to 404 — service already
# filters non-file / directory, so the handler's None branch is exercised
# by the resource-missing case). Download returns a raw ``Response`` with
# no envelope, so there is no x-trace-id assertion.


@pytest.mark.asyncio
async def test_download_returns_raw_bytes_with_mime_type():
    service = _StubService([])
    factory, bot_repo, resolver, dispatcher, device_fs = _delete_deps(service=service)

    response = await download_resource(
        resource_id="1", principal=None,
        bot_id="bot-x", factory=factory,
        bot_repo=bot_repo, resolver=resolver,
        device_fs_dispatcher=dispatcher,
    )

    # Raw Response (no envelope): body is the bytes, media_type is the mime.
    assert isinstance(response, Response)
    assert response.body == b"file content"
    assert response.media_type == "application/pdf"
    # device_fs resolved by the dispatcher was forwarded to the service.
    assert service.last_call_device_fs is device_fs
    # bot_id flowed through to factory.create.
    assert factory.created_bot_ids == ["bot-x"]


@pytest.mark.asyncio
async def test_download_raises_404_when_bot_missing():
    factory, bot_repo, resolver, dispatcher, _ = _delete_deps(bot_dict=None)

    with pytest.raises(HTTPException) as exc:
        await download_resource(
            resource_id="1", principal=None,
            bot_id="ghost", factory=factory,
            bot_repo=bot_repo, resolver=resolver,
            device_fs_dispatcher=dispatcher,
        )

    assert exc.value.status_code == 404
    assert "Bot" in exc.value.detail
    assert "ghost" in exc.value.detail


@pytest.mark.asyncio
async def test_download_raises_404_when_service_returns_none():
    # service.download_resource returns None for resource_id == "0"
    # (simulates not-found / not-a-file / is-directory / read-failure —
    # the service collapses all to None and the handler maps to 404).
    service = _StubService([])
    factory, bot_repo, resolver, dispatcher, _ = _delete_deps(service=service)

    with pytest.raises(HTTPException) as exc:
        await download_resource(
            resource_id="0", principal=None,
            bot_id="bot-a", factory=factory,
            bot_repo=bot_repo, resolver=resolver,
            device_fs_dispatcher=dispatcher,
        )

    assert exc.value.status_code == 404
    assert "not downloadable" in exc.value.detail


# ── preview_resource (Phase 3 Task 5 — fourth real device_fs handler) ─
#
# Same device_fs resolution chain as delete / upload / download
# (bot_repo → owner_id → resolver.resolve_for_bot → dispatcher.dispatch →
# device_fs). Like download / upload, preview does NOT call get_device_info
# (that's a delete-only concern for picking arca/local). preview_resource
# on the concrete service is ASYNC — the handler must ``await`` it. Service
# returns a dict {content, content_type, size} or None → 404 (not-found /
# not-a-file / is-directory / read-failure / empty all collapse to 404 —
# service filters non-file / directory / empty). ValueError (content > 1
# MB cap) → 413 (legacy parity). Unlike download (raw Response), preview
# returns an enveloped Preview schema so the caller gets a structured
# content_type + content pair.


@pytest.mark.asyncio
async def test_preview_returns_envelope_with_content_and_type():
    service = _StubService([])
    factory, bot_repo, resolver, dispatcher, device_fs = _delete_deps(service=service)

    env = await preview_resource(
        resource_id="1", principal=None,
        bot_id="bot-x", factory=factory,
        bot_repo=bot_repo, resolver=resolver,
        device_fs_dispatcher=dispatcher, request=None,
    )

    assert isinstance(env, Envelope)
    assert env.code == CODE_OK
    assert env.message == "OK"
    assert env.data is not None
    assert isinstance(env.data, Preview)
    assert env.data.resource_id == "1"
    assert env.data.content_type == "text/plain"
    assert env.data.content == "preview body"
    # preview_url is intentionally left unset (no signed-URL path in Phase 3).
    assert env.data.preview_url is None
    # device_fs resolved by the dispatcher was forwarded to the service.
    assert service.last_call_device_fs is device_fs
    # bot_id flowed through to factory.create.
    assert factory.created_bot_ids == ["bot-x"]


@pytest.mark.asyncio
async def test_preview_raises_404_when_bot_missing():
    factory, bot_repo, resolver, dispatcher, _ = _delete_deps(bot_dict=None)

    with pytest.raises(HTTPException) as exc:
        await preview_resource(
            resource_id="1", principal=None,
            bot_id="ghost", factory=factory,
            bot_repo=bot_repo, resolver=resolver,
            device_fs_dispatcher=dispatcher, request=None,
        )

    assert exc.value.status_code == 404
    assert "Bot" in exc.value.detail
    assert "ghost" in exc.value.detail


@pytest.mark.asyncio
async def test_preview_raises_404_when_service_returns_none():
    # service.preview_resource returns None for resource_id == "0" (simulates
    # not-found / not-a-file / is-directory / read-failure / empty — the
    # service collapses all to None and the handler maps to 404).
    service = _StubService([])
    factory, bot_repo, resolver, dispatcher, _ = _delete_deps(service=service)

    with pytest.raises(HTTPException) as exc:
        await preview_resource(
            resource_id="0", principal=None,
            bot_id="bot-a", factory=factory,
            bot_repo=bot_repo, resolver=resolver,
            device_fs_dispatcher=dispatcher, request=None,
        )

    assert exc.value.status_code == 404
    assert "not previewable" in exc.value.detail


@pytest.mark.asyncio
async def test_preview_raises_413_when_too_large():
    # service.preview_resource raises ValueError for resource_id == "too-large"
    # (simulates content > 1 MB cap); the handler maps ValueError → 413.
    service = _StubService([])
    factory, bot_repo, resolver, dispatcher, _ = _delete_deps(service=service)

    with pytest.raises(HTTPException) as exc:
        await preview_resource(
            resource_id="too-large", principal=None,
            bot_id="bot-a", factory=factory,
            bot_repo=bot_repo, resolver=resolver,
            device_fs_dispatcher=dispatcher, request=None,
        )

    assert exc.value.status_code == 413
    assert "too large" in exc.value.detail
    # The ValueError message surfaces through HTTPException.detail (legacy
    # parity: "File too large for preview (max N bytes)").
    assert "1" in exc.value.detail  # the cap value


# ── End-to-end integration (review #8) ──────────────────────────────
#
# The stub-service unit tests above pass even when the real factory's
# service is missing methods (the stub supplies them). This block uses the
# REAL ``ResourceServiceFactory`` (factory.create → real slim
# ``ResourceService``, not a stub) backed by a REAL in-memory repository
# (non-mock, so the slim service's real create/get/delete logic actually
# runs) + a stub device_fs. A missing method or a handler↔service signature
# mismatch fails here instead of in production.


class _InMemoryResourceRepo:
    """Minimal real ``ResourceRepositoryProtocol`` for integration tests.

    Non-mock: create stores a row, get_by_id reads it back, delete
    soft-deletes it. The slim service's real logic runs against this, so
    the create→get→list→delete round-trip is exercised end-to-end
    (only the storage layer is faked — everything above it is production
    code).
    """

    def __init__(self) -> None:
        self._rows: dict[str, dict] = {}
        self._next_id = 1

    def get_by_id(self, resource_id: str) -> dict | None:
        row = self._rows.get(str(resource_id))
        return dict(row) if row else None

    def get_by_path(self, path: str, bolt_id: str | None = None) -> dict | None:
        for row in self._rows.values():
            attrs = row.get("attributes") or {}
            if attrs.get("path") == path:
                return dict(row)
        return None

    def list_resources(
        self,
        resource_type: str | None = None,
        parent_path: str | None = None,
        user_id: str | None = None,
        status: str | None = None,
        bolt_id: str | None = None,
    ) -> list[dict]:
        results: list[dict] = []
        for row in self._rows.values():
            if resource_type and row.get("resource_type") != resource_type:
                continue
            if bolt_id and row.get("bolt_id") != bolt_id:
                continue
            if parent_path is not None:
                attrs = row.get("attributes") or {}
                if attrs.get("parent_path") != parent_path:
                    continue
            if user_id is not None and row.get("user_id") != user_id:
                continue
            if status and row.get("status") != status:
                continue
            results.append(dict(row))
        return results

    def create(self, resource_data: dict) -> dict:
        data = dict(resource_data)
        new_id = self._next_id
        self._next_id += 1
        data["id"] = new_id
        self._rows[str(new_id)] = data
        return dict(data)

    def update(self, resource_id: str, resource_data: dict) -> dict | None:
        row = self._rows.get(str(resource_id))
        if not row:
            return None
        row.update(resource_data)
        return dict(row)

    def delete(self, resource_id: str) -> bool:
        row = self._rows.get(str(resource_id))
        if not row:
            return False
        row["status"] = "deleted"
        return True

    def hard_delete(self, resource_id: str) -> bool:
        return self._rows.pop(str(resource_id), None) is not None

    def count_resources(
        self,
        resource_type: str | None = None,
        parent_path: str | None = None,
        user_id: str | None = None,
        status: str | None = None,
        bolt_id: str | None = None,
    ) -> int:
        return len(
            self.list_resources(
                resource_type=resource_type,
                parent_path=parent_path,
                user_id=user_id,
                status=status,
                bolt_id=bolt_id,
            )
        )


def _real_factory_with_inmemory_repo() -> tuple[ResourceServiceFactory, _InMemoryResourceRepo]:
    """Construct the REAL factory backed by a real in-memory repository.

    ``ResourceServiceFactory.__init__`` is ``@inject``-decorated, but
    direct construction with an explicit ``repository=`` bypasses injection
    (same path the injector takes in prod). ``factory.create(bot_id=…)``
    returns the real slim ``ResourceService`` — the exact object the
    openapi_v1 handlers receive.
    """
    repo = _InMemoryResourceRepo()
    factory = ResourceServiceFactory(repository=repo)
    return factory, repo


@pytest.mark.asyncio
async def test_real_factory_service_supports_all_handler_methods_e2e():
    """End-to-end: real factory → real slim service → all 5 new methods.

    Exercises the full create→get→list→upload→download→preview→delete
    round-trip through:

    - the REAL ``ResourceServiceFactory.create`` (factory bug surface),
    - the REAL slim ``ResourceService`` (the 5 methods added in review #4),
    - a REAL in-memory repository (create/get/delete actually run),
    - a stub device_fs (file bytes round-trip through write_file/read_file).

    A missing method on the slim service (the review #4 bug) or a
    handler↔service signature mismatch (review #8) fails here.
    """
    factory, repo = _real_factory_with_inmemory_repo()
    device_fs = _StubDeviceFs()
    bot_repo = _StubBotRepo(bot_dict=_DEFAULT_BOT)
    resolver = _StubResolver()
    dispatcher = _StubDispatcher(device_fs)

    # 1. create a LINK via the create handler (exercises factory.create →
    #    service.create_url_resource, already present on the slim service).
    env = await create_resource(
        body=ResourceCreate(name="doc", type=OpenapiType.LINK, url="https://x.com"),
        principal=None, bot_id="bot-x", factory=factory, request=None,
    )
    assert env.code == CODE_CREATED
    link_id = env.data.resource_id
    assert link_id  # factory really persisted via repo.create

    # 2. get it back (exercises factory.create → service.get_resource —
    #    the new slim method). The real repo round-trips the stored row.
    env = await get_resource(
        resource_id=link_id, principal=None, bot_id="bot-x",
        factory=factory, request=None,
    )
    assert env.code == CODE_OK
    assert env.data.resource_id == link_id
    assert env.data.name == "doc"
    assert env.data.type == OpenapiType.LINK
    assert env.data.url == "https://x.com"

    # 3. list (exercises factory.create → service.list_resources — confirms
    #    the persisted row is visible to the real repo).
    env = await list_resources(
        page=PageParams(), principal=None, bot_id="bot-x",
        type=None, factory=factory, request=None,
    )
    assert env.data.total >= 1
    assert any(item.resource_id == link_id for item in env.data.items)

    # 4. upload a FILE (exercises factory.create → service.upload_file —
    #    the new slim method; device_fs.write_file is called by real service
    #    logic, and the real repo stores the FILE record).
    env = await upload_resource(
        name="hello.txt", content=b"file bytes",
        principal=None, bot_id="bot-x", factory=factory,
        bot_repo=bot_repo, resolver=resolver,
        device_fs_dispatcher=dispatcher, request=None,
    )
    assert env.code == CODE_CREATED
    file_id = env.data.resource_id
    assert env.data.type == OpenapiType.FILE
    assert env.data.size == len(b"file bytes")
    # real service really invoked device_fs.write_file
    assert device_fs.written == [("hello.txt", b"file bytes")]

    # 5. download the FILE (exercises factory.create →
    #    service.download_resource — new slim method; device_fs.read_file
    #    round-trips the bytes written in step 4).
    response = await download_resource(
        resource_id=file_id, principal=None, bot_id="bot-x",
        factory=factory, bot_repo=bot_repo, resolver=resolver,
        device_fs_dispatcher=dispatcher,
    )
    assert response.body == b"file bytes"
    assert response.media_type == "application/octet-stream"

    # 6. preview the FILE (exercises factory.create →
    #    service.preview_resource — new slim method; real service decodes
    #    the bytes and returns the {content, content_type, size} dict).
    env = await preview_resource(
        resource_id=file_id, principal=None, bot_id="bot-x",
        factory=factory, bot_repo=bot_repo, resolver=resolver,
        device_fs_dispatcher=dispatcher, request=None,
    )
    assert env.code == CODE_OK
    assert env.data.content == "file bytes"
    assert env.data.content_type == "application/octet-stream"

    # 7. delete the FILE (exercises factory.create → service.delete_resource
    #    — new slim method; real service awaits device_fs.delete_file for the
    #    FILE resource, then soft-deletes the repo row).
    env = await delete_resource(
        resource_id=file_id, principal=None, bot_id="bot-x",
        factory=factory, bot_repo=bot_repo, resolver=resolver,
        device_fs_dispatcher=dispatcher, request=None,
    )
    assert env.code == CODE_OK
    assert env.data.deleted is True
    # real service really invoked device_fs.delete_file with the stored path
    assert device_fs.deleted_paths == ["hello.txt"]
    # second delete → service reports False (already soft-deleted row still
    # resolves, but a missing-id delete returns False). Drive a bogus id
    # directly to exercise the not-found → 404 path.
    with pytest.raises(HTTPException) as exc:
        await delete_resource(
            resource_id="999999", principal=None, bot_id="bot-x",
            factory=factory, bot_repo=bot_repo, resolver=resolver,
            device_fs_dispatcher=dispatcher, request=None,
        )
    assert exc.value.status_code == 404

    # 8. duplicate-name upload surfaces ValueError → 409 through the REAL
    #    slim service's check_name_exists path (repo.list_resources really
    #    sees the prior FILE row).
    with pytest.raises(HTTPException) as exc:
        await upload_resource(
            name="hello.txt", content=b"dup",
            principal=None, bot_id="bot-x", factory=factory,
            bot_repo=bot_repo, resolver=resolver,
            device_fs_dispatcher=dispatcher, request=None,
        )
    assert exc.value.status_code == 409
    assert "already exists" in exc.value.detail


# ── Fix #1 (review round-2): enum mapping contract ──────────────────
#
# Round-1 wired the openapi ResourceType→legacy ResourceType seam as
# ``type.value if type else None`` — passing a STRING where the slim
# service expects the legacy ResourceType ENUM (the service internally
# does ``resource_type.value``). Strings have no ``.value`` →
# AttributeError. These tests pin the new ``_legacy_type_for`` contract:
# the value handed to the service IS the legacy enum (identity check,
# not just str equality which would pass either way).


@pytest.mark.asyncio
async def test_list_resources_passes_legacy_enum_to_service():
    service = _StubService([])
    factory = _StubFactory(service)

    await list_resources(
        page=PageParams(), principal=None, bot_id="bot-a",
        type=OpenapiType.LINK, factory=factory, request=None,
    )

    passed = service.last_call_kwargs.get("resource_type")
    assert passed is LegacyType.LINK
    assert passed is not OpenapiType.LINK


@pytest.mark.asyncio
async def test_list_resources_passes_none_when_type_filter_absent():
    service = _StubService([])
    factory = _StubFactory(service)

    await list_resources(
        page=PageParams(), principal=None, bot_id="bot-a",
        type=None, factory=factory, request=None,
    )

    # _legacy_type_for(None) returns None — no filter.
    assert service.last_call_kwargs.get("resource_type") is None


@pytest.mark.asyncio
async def test_list_resources_openapi_folder_has_no_legacy_counterpart():
    """openapi FOLDER has no legacy enum equivalent. List with type=FOLDER
    MUST return an empty page — NOT fall through to resource_type=None which
    the slim service treats as "no filter" and would surface every row
    (FILE/LINK) under a FOLDER query. This pins that guard (the bug:
    unfiltered list leaked all rows when asked for FOLDER)."""
    # Real factory + repo seeded with FILE and LINK rows (NOT empty) — an
    # unfiltered list would return them; a correct FOLDER filter returns none.
    factory, repo = _real_factory_with_inmemory_repo()
    file_id = repo.create(Resource(
        id=1, name="f1", resource_type=LegacyType.FILE,
        attributes={"path": "/f", "size": 5}, bolt_id="bot-a",
    ).to_dict())
    link_id = repo.create(create_link_resource(
        name="l1", url="https://x.com", link_type="external", id=2,
        bolt_id="bot-a",
    ).to_dict())

    # Sanity: an unfiltered list DOES see both rows (proves the repo is
    # non-empty and the leak would surface them without the FOLDER guard).
    unfiltered = await list_resources(
        page=PageParams(), principal=None, bot_id="bot-a",
        type=None, factory=factory, request=None,
    )
    assert unfiltered.data.total >= 2

    # type=FOLDER must NOT leak those rows — empty page, not "all".
    env = await list_resources(
        page=PageParams(), principal=None, bot_id="bot-a",
        type=OpenapiType.FOLDER, factory=factory, request=None,
    )
    assert env.code == CODE_OK
    assert env.data.total == 0
    assert env.data.items == []


@pytest.mark.asyncio
async def test_check_name_passes_legacy_enum_to_service_when_provided():
    service = _StubService([])
    factory = _StubFactory(service)

    await check_resource_name(
        name="available", principal=None, type=OpenapiType.FILE,
        bot_id="bot-a", factory=factory, request=None,
    )

    passed = service.last_call_kwargs.get("resource_type")
    assert passed is LegacyType.FILE
    assert passed is not OpenapiType.FILE


@pytest.mark.asyncio
async def test_check_name_defaults_to_legacy_file_enum_when_type_absent():
    service = _StubService([])
    factory = _StubFactory(service)

    await check_resource_name(
        name="available", principal=None, type=None,
        bot_id="bot-a", factory=factory, request=None,
    )

    # ``or _LegacyType.FILE`` is the documented default for the no-type case.
    assert service.last_call_kwargs.get("resource_type") is LegacyType.FILE


# ── Fix #2 (review round-2): cross-bot ownership isolation ──────────
#
# get / delete / download / preview had ZERO bolt_id ownership check
# (round-1 regression): the only guard before round-2 was
# update_link_resource. A cross-bot resource_id would happily read /
# delete / download / preview a foreign bot's resource. Fix #2 collapses
# cross-bot access to None (→404 for get/download/preview) / False
# (→404 for delete), matching update_link_resource's invariant. These
# tests back the check with the REAL slim service + REAL in-memory repo
# so the bug surface is exactly what production sees.


def _seed_foreign_resource(repo, *, bolt_id="other-bot") -> str:
    """Insert a row owned by a foreign bolt_id and return its str(id).

    Mirrors the shape ``_dict_to_resource`` accepts (Resource.id is
    Optional[Any] so int ids are fine; the store path uses str keys).
    """
    from datetime import datetime
    ts = datetime(2026, 7, 28, 10, 0).isoformat()
    stored = repo.create({
        "name": "foreign-link",
        "resource_type": "link",
        "status": "active",
        "gmt_created": ts,
        "gmt_modified": ts,
        "attributes": {"url": "https://foreign.example", "link_type": "external"},
        "user_id": "u-foreign",
        "bolt_id": bolt_id,
    })
    return str(stored["id"])


@pytest.mark.asyncio
async def test_get_resource_returns_404_for_cross_bot_resource_id():
    factory, repo = _real_factory_with_inmemory_repo()
    foreign_id = _seed_foreign_resource(repo)

    with pytest.raises(HTTPException) as exc:
        await get_resource(
            resource_id=foreign_id, principal=None, bot_id="bot-x",
            factory=factory, request=None,
        )
    assert exc.value.status_code == 404
    assert exc.value.detail == "Resource not found"


@pytest.mark.asyncio
async def test_delete_resource_returns_404_for_cross_bot_resource_id():
    factory, repo = _real_factory_with_inmemory_repo()
    foreign_id = _seed_foreign_resource(repo)
    bot_repo = _StubBotRepo(bot_dict=_DEFAULT_BOT)
    resolver = _StubResolver()
    dispatcher = _StubDispatcher(_StubDeviceFs())

    with pytest.raises(HTTPException) as exc:
        await delete_resource(
            resource_id=foreign_id, principal=None, bot_id="bot-x",
            factory=factory, bot_repo=bot_repo, resolver=resolver,
            device_fs_dispatcher=dispatcher, request=None,
        )
    assert exc.value.status_code == 404
    assert exc.value.detail == "Resource not found"
    # Foreign row must NOT be soft-deleted by the cross-bot delete call.
    row = repo.get_by_id(foreign_id)
    assert row.get("status") == "active"


@pytest.mark.asyncio
async def test_download_resource_returns_404_for_cross_bot_resource_id():
    factory, repo = _real_factory_with_inmemory_repo()
    foreign_id = _seed_foreign_resource(repo)
    bot_repo = _StubBotRepo(bot_dict=_DEFAULT_BOT)
    resolver = _StubResolver()
    dispatcher = _StubDispatcher(_StubDeviceFs())

    with pytest.raises(HTTPException) as exc:
        await download_resource(
            resource_id=foreign_id, principal=None, bot_id="bot-x",
            factory=factory, bot_repo=bot_repo, resolver=resolver,
            device_fs_dispatcher=dispatcher,
        )
    assert exc.value.status_code == 404
    assert "not downloadable" in exc.value.detail


@pytest.mark.asyncio
async def test_preview_resource_returns_404_for_cross_bot_resource_id():
    factory, repo = _real_factory_with_inmemory_repo()
    foreign_id = _seed_foreign_resource(repo)
    bot_repo = _StubBotRepo(bot_dict=_DEFAULT_BOT)
    resolver = _StubResolver()
    dispatcher = _StubDispatcher(_StubDeviceFs())

    with pytest.raises(HTTPException) as exc:
        await preview_resource(
            resource_id=foreign_id, principal=None, bot_id="bot-x",
            factory=factory, bot_repo=bot_repo, resolver=resolver,
            device_fs_dispatcher=dispatcher, request=None,
        )
    assert exc.value.status_code == 404
    assert "not previewable" in exc.value.detail


@pytest.mark.asyncio
async def test_same_bot_get_works_after_isolation_invariant():
    """Control: same-bot read still returns 200 — the ownership guard
    doesn't accidentally quarantine the bot's own resources."""
    factory, repo = _real_factory_with_inmemory_repo()
    own_id = _seed_foreign_resource(repo, bolt_id="bot-x")

    env = await get_resource(
        resource_id=own_id, principal=None, bot_id="bot-x",
        factory=factory, request=None,
    )
    assert env.code == CODE_OK
    assert env.data.resource_id == own_id
    assert env.data.type == OpenapiType.LINK


# ── Fix #3 (review round-2): upload surfaces device_fs write failure as 502 ─
#
# Round-1 ``upload_file`` did ``try ... except Exception: logger.warning(...)``
# around ``device_fs.write_file`` then ran ``repo.create`` anyway — file
# write failed but the handler returned 201 with a phantom record pointing
# at a path with no bytes. Fix #3 lets the write exception bubble so the
# handler is the single translation point: any non-ValueError from
# ``upload_file`` → 502 Bad Gateway AND no DB row created.


class _FailingDeviceFs:
    """device_fs stub where every op raises — exercises the 502 path."""

    async def write_file(self, path, data):
        raise OSError("disk full")

    async def read_file(self, file_path, *, enforce_download_limit=False):
        raise OSError("disk read failure")

    async def delete_file(self, path):
        raise OSError("disk delete failure")


@pytest.mark.asyncio
async def test_upload_returns_502_when_device_fs_write_fails():
    factory, repo = _real_factory_with_inmemory_repo()
    bot_repo = _StubBotRepo(bot_dict=_DEFAULT_BOT)
    resolver = _StubResolver()
    dispatcher = _StubDispatcher(_FailingDeviceFs())
    rows_before = len(repo._rows)

    with pytest.raises(HTTPException) as exc:
        await upload_resource(
            name="hello.txt", content=b"file bytes",
            principal=None, bot_id="bot-x", factory=factory,
            bot_repo=bot_repo, resolver=resolver,
            device_fs_dispatcher=dispatcher, request=None,
        )
    assert exc.value.status_code == 502
    assert "Upload storage failed" in exc.value.detail
    # No DB row created — write failure short-circuited before repo.create.
    assert len(repo._rows) == rows_before


@pytest.mark.asyncio
async def test_upload_409_takes_precedence_over_502_path():
    """When both conditions hold (duplicate name AND a failing device_fs),
    the slim service runs check_name_exists FIRST (before write_file), so
    the 409 surfaces — not the 502. Pins the service's ordering invariance."""
    from datetime import datetime
    factory, repo = _real_factory_with_inmemory_repo()
    bot_repo = _StubBotRepo(bot_dict=_DEFAULT_BOT)
    resolver = _StubResolver()
    dispatcher = _StubDispatcher(_FailingDeviceFs())
    # Seed a bot-x FILE row whose name collides with the upload below.
    ts = datetime(2026, 7, 28, 10, 0).isoformat()
    repo.create({
        "name": "hello.txt",
        "resource_type": "file",
        "status": "active",
        "gmt_created": ts,
        "gmt_modified": ts,
        "attributes": {"path": "hello.txt", "size": 1},
        "user_id": "own-a",
        "bolt_id": "bot-x",
    })

    with pytest.raises(HTTPException) as exc:
        await upload_resource(
            name="hello.txt", content=b"x",
            principal=None, bot_id="bot-x", factory=factory,
            bot_repo=bot_repo, resolver=resolver,
            device_fs_dispatcher=dispatcher, request=None,
        )
    assert exc.value.status_code == 409
    assert "already exists" in exc.value.detail
