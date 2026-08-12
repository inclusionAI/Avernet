"""openapi_v1 resources handler unit tests: mapping + handler behavior."""

import json
from datetime import datetime
from types import SimpleNamespace
from typing import List

import pytest
from fastapi import HTTPException, Request, Response

from agentclaw.community.adapters.http.openapi_v1 import build_public_router
from agentclaw.community.adapters.http.openapi_v1.errors import MissingPrincipalError
from agentclaw.community.adapters.http.openapi_v1.principal import require_user_id
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
    create_directory,
    create_resource,
    delete_file,
    delete_resource,
    download_file,
    get_resource,
    list_resources,
    preview_file,
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
from agentclaw.community.core.devices.services.device_filesystem import (
    FileTooLargeError as DeviceFileTooLargeError,
)
from agentclaw.community.core.resources.service import (
    DuplicateResourceError,
    FileTooLargeError,
    ResourceNotFoundError,
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


def _request_scope() -> dict:
    """Minimal ASGI scope for a stubbed http request (no live server)."""
    return {
        "type": "http",
        "method": "GET",
        "headers": [],
        "path": "/",
        "query_string": b"",
    }


def _request_without_trace() -> Request:
    """A request whose tracer middleware did not run — ``state.trace_id`` unset.

    ``responses._trace_id`` reads ``request.state.trace_id`` and falls back to
    ``""`` when absent, so the envelope's ``request_id`` is empty (mirrors the
    prod path before the tracer middleware stamps the id). A real
    ``fastapi.Request`` (not a ``SimpleNamespace``) so ``@envelope_errors``'
    ``_find_request`` recognises it on the error path.
    """
    return Request(_request_scope())


def _request_with_trace(trace_id: str) -> Request:
    """A request whose tracer middleware stamped ``trace_id`` on ``state``."""
    req = Request(_request_scope())
    req.state.trace_id = trace_id
    return req


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


# ── list_resources handler wiring (Phase 1 Task 2) ──────────────────────
#
# Direct handler invocation (退路 B per task spec): bypasses FastAPI's
# dependency wiring and supplies a stub factory. `principal` is `Any`, so
# `None` is an acceptable stand-in. Handlers take a required `request: Request`
# (mirroring the bots router); tests pass a `SimpleNamespace` stub whose
# `state.trace_id` is either unset (empty `request_id`) or set to a known
# value (asserted into the envelope via `responses.envelope`).


class _StubService:
    """Minimal stub satisfying the ResourceServiceProtocol list_resources seam."""

    def __init__(self, items: List[Resource]) -> None:
        self._items = items
        self.last_call_kwargs: dict = {}
        self.recorded: List[dict] = []
        self.record_deletes: List[str] = []
        self.deleted: List[str] = []

    def list_resources(self, *args, **kwargs):
        self.last_call_kwargs = dict(kwargs)
        items = self._items
        limit = kwargs.get("limit")
        offset = kwargs.get("offset", 0) or 0
        if limit:
            items = items[offset : offset + limit]
        return items

    def count_resources(self, *, resource_type=None) -> int:
        return len(self._items)

    def get_resource(self, resource_id):
        # Sync lookup (matches concrete ResourceService.get_resource).
        # Captures the call kwargs so tests can assert what was passed.
        self.last_call_kwargs = dict(resource_id=resource_id)
        for r in self._items:
            if str(r.id) == str(resource_id):
                return r
        return None

    async def record_uploaded_file(
        self, *, path, size, user_id=None, created_by=None, source="upload"
    ):
        self.recorded.append(
            {"path": path, "size": size, "user_id": user_id, "created_by": created_by}
        )
        return _legacy(
            id="rec-1",
            name=path.rsplit("/", 1)[-1],
            resource_type=LegacyType.FILE,
            attributes={"path": path, "size": size},
            source=source,
        )

    async def delete_file_record(self, *, path) -> bool:
        self.record_deletes.append(path)
        return True

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
            name=name,
            url=url,
            method=method,
            parent_path=parent_path,
            user_id=user_id,
            created_by=created_by,
        )
        if name == "taken":
            raise DuplicateResourceError(f"Resource '{name}' already exists")
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
            resource_id=resource_id,
            link_type=link_type,
            url=url,
            name=name,
        )
        # resource_id == "0" simulates a missing record (service raises
        # ResourceNotFoundError → 404 via @envelope_errors).
        if str(resource_id) == "0":
            raise ResourceNotFoundError("Resource not found")
        # url == "conflict" simulates a URL uniqueness violation.
        if url == "conflict":
            raise DuplicateResourceError("URL already exists")
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
        self.deleted.append(str(resource_id))
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
            data=data,
            filename=filename,
            parent_path=parent_path,
            user_id=user_id,
        )
        self.last_call_device_fs = device_fs
        if filename == "taken":
            raise DuplicateResourceError(f"Resource '{filename}' already exists")
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
            raise FileTooLargeError(
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
async def test_list_returns_workspace_entries_not_records():
    """Files and folders come from the workspace. A record-backed listing could
    not see a file the bot produced itself — it has no record and never will."""
    file_svc = _StubListFileService([
        _listed("notes.md", rel="notes.md", size=12),
        _listed("docs", rel="docs", is_dir=True),
    ])

    env = await list_resources(
        page=PageParams(),
        owner_id="u1",
        bot_id="bot-x",
        path="",
        type=None,
        factory=_StubFactory(_StubService([])),
        bot_repo=_StubBotRepo(),
        file_svc=file_svc,
        request=_request_without_trace(),
    )

    assert env.data.total == 2
    by_name = {i.name: i for i in env.data.items}
    assert by_name["notes.md"].type == OpenapiType.FILE
    assert by_name["notes.md"].path == "notes.md"
    assert by_name["notes.md"].size == 12
    assert by_name["docs"].type == OpenapiType.FOLDER
    # No record backs a workspace entry.
    assert all(i.resource_id == "" for i in env.data.items)
    assert all(i.gmt_create is None for i in env.data.items)


@pytest.mark.asyncio
async def test_list_joins_the_listed_directory_onto_entry_paths():
    """The device reports ``relative_path`` relative to the listed directory, so
    listing ``a/b`` must yield ``a/b/c.txt`` — the address a client hands back."""
    file_svc = _StubListFileService([
        _listed("c.txt", rel="a/b/c.txt"),
    ])

    env = await list_resources(
        page=PageParams(),
        owner_id="u1",
        bot_id="bot-x",
        path="a/b",
        type=None,
        factory=_StubFactory(_StubService([])),
        bot_repo=_StubBotRepo(),
        file_svc=file_svc,
        request=_request_without_trace(),
    )

    assert env.data.items[0].path == "a/b/c.txt"
    assert file_svc.listed == ["a/b"]


@pytest.mark.asyncio
async def test_list_never_exposes_the_container_path():
    """The entry's own ``path`` is the engine-view absolute container path and
    must not cross a public API."""
    file_svc = _StubListFileService([
        _listed("c.txt", rel="c.txt"),
    ])

    env = await list_resources(
        page=PageParams(), owner_id="u1", bot_id="bot-x", path="", type=None,
        factory=_StubFactory(_StubService([])), bot_repo=_StubBotRepo(),
        file_svc=file_svc, request=_request_without_trace(),
    )

    assert "/home/admin" not in (env.data.items[0].path or "")


@pytest.mark.asyncio
async def test_list_appends_links_which_have_no_file():
    """A link has no file and no device presence, so the record is the resource."""
    link = _legacy(id="l1", name="wiki")
    file_svc = _StubListFileService([
        _listed("a.txt", rel="a.txt"),
    ])

    env = await list_resources(
        page=PageParams(), owner_id="u1", bot_id="bot-x", path="", type=None,
        factory=_StubFactory(_StubService([link])), bot_repo=_StubBotRepo(),
        file_svc=file_svc, request=_request_without_trace(),
    )

    kinds = {i.type for i in env.data.items}
    assert kinds == {OpenapiType.FILE, OpenapiType.LINK}
    assert env.data.total == 2


@pytest.mark.asyncio
async def test_list_includes_legacy_url_rows_as_links():
    """Two legacy types collapse into openapi LINK — ``LINK`` and the older
    ``URL`` — so narrowing the repo query to either one silently drops the
    other. Rows are fetched unfiltered and narrowed by the *mapped* type."""
    url_row = _legacy(id="u9", name="yuque-doc", resource_type=LegacyType.URL)

    env = await list_resources(
        page=PageParams(), owner_id="u1", bot_id="bot-x", path="", type=None,
        factory=_StubFactory(_StubService([url_row])), bot_repo=_StubBotRepo(),
        file_svc=_StubListFileService([]), request=_request_without_trace(),
    )

    assert [i.name for i in env.data.items] == ["yuque-doc"]
    assert env.data.items[0].type == OpenapiType.LINK


@pytest.mark.asyncio
async def test_list_never_reports_file_rows():
    """A record is not evidence a file exists — that is the divergence this
    change removes. File rows are excluded; the workspace half is authoritative."""
    file_row = _legacy(id="f1", name="ghost.txt", resource_type=LegacyType.FILE,
                       attributes={"path": "ghost.txt"})

    env = await list_resources(
        page=PageParams(), owner_id="u1", bot_id="bot-x", path="", type=None,
        factory=_StubFactory(_StubService([file_row])), bot_repo=_StubBotRepo(),
        file_svc=_StubListFileService([]), request=_request_without_trace(),
    )

    assert env.data.items == []


@pytest.mark.asyncio
async def test_list_folder_filter_returns_directories():
    """FOLDER used to short-circuit to an empty page — there were no folder rows
    to return. Directories are real on the filesystem, so it now returns them."""
    file_svc = _StubListFileService([
        _listed("docs", rel="docs", is_dir=True),
        _listed("a.txt", rel="a.txt"),
    ])

    env = await list_resources(
        page=PageParams(), owner_id="u1", bot_id="bot-x", path="",
        type=OpenapiType.FOLDER,
        factory=_StubFactory(_StubService([_legacy(id="l1", name="wiki")])),
        bot_repo=_StubBotRepo(), file_svc=file_svc,
        request=_request_without_trace(),
    )

    assert [i.name for i in env.data.items] == ["docs"]


@pytest.mark.asyncio
async def test_list_link_filter_does_not_touch_the_device():
    file_svc = _StubListFileService([
        _listed("a.txt", rel="a.txt"),
    ])

    env = await list_resources(
        page=PageParams(), owner_id="u1", bot_id="bot-x", path="",
        type=OpenapiType.LINK,
        factory=_StubFactory(_StubService([_legacy(id="l1", name="wiki")])),
        bot_repo=_StubBotRepo(), file_svc=file_svc,
        request=_request_without_trace(),
    )

    assert [i.type for i in env.data.items] == [OpenapiType.LINK]
    assert file_svc.listed == []  # no device round trip for a link-only listing


@pytest.mark.asyncio
async def test_list_paginates_across_both_sources():
    file_svc = _StubListFileService([
        _listed(f"f{n}.txt", rel=f"f{n}.txt") for n in range(3)
    ])

    env = await list_resources(
        page=PageParams(page=2, page_size=2),
        owner_id="u1", bot_id="bot-x", path="", type=None,
        factory=_StubFactory(_StubService([_legacy(id="l1", name="wiki")])),
        bot_repo=_StubBotRepo(), file_svc=file_svc,
        request=_request_without_trace(),
    )

    # total spans the merged view; the page is the slice.
    assert env.data.total == 4
    assert len(env.data.items) == 2


@pytest.mark.asyncio
async def test_list_reads_x_trace_id_from_request():
    env = await list_resources(
        page=PageParams(), owner_id="u1", bot_id="bot-x", path="", type=None,
        factory=_StubFactory(_StubService([])), bot_repo=_StubBotRepo(),
        file_svc=_StubListFileService([]),
        request=_request_with_trace("trace-list-1"),
    )
    assert env.request_id == "trace-list-1"


@pytest.mark.asyncio
async def test_list_empty_workspace_returns_empty_page():
    env = await list_resources(
        page=PageParams(), owner_id="u1", bot_id="bot-x", path="", type=None,
        factory=_StubFactory(_StubService([])), bot_repo=_StubBotRepo(),
        file_svc=_StubListFileService([]), request=_request_without_trace(),
    )
    assert env.data.total == 0
    assert env.data.items == []


@pytest.mark.asyncio
async def test_list_rejects_a_directory_escaping_the_workspace():
    file_svc = _StubListFileService([])

    resp = await list_resources(
        page=PageParams(), owner_id="u1", bot_id="bot-x", path="../../etc",
        type=None, factory=_StubFactory(_StubService([])), bot_repo=_StubBotRepo(),
        file_svc=file_svc, request=_request_without_trace(),
    )

    assert resp.status_code == 400
    assert file_svc.listed == []

# ── check_resource_name: files ask the workspace, links ask the records ──


@pytest.mark.asyncio
async def test_check_name_asks_the_workspace_for_a_file_path():
    """Same question the upload asks before writing, answered by the same
    authority — so the two can never disagree."""
    file_svc = _StubFileService(existing={"docs/a.txt"})

    env = await check_resource_name(
        path="docs/a.txt",
        owner_id="u1",
        bot_id="bot-x",
        factory=_StubFactory(_StubService([])),
        bot_repo=_StubBotRepo(),
        file_svc=file_svc,
        request=_request_without_trace(),
    )

    assert env.data.exists is True
    assert file_svc.exists_calls == ["docs/a.txt"]


@pytest.mark.asyncio
async def test_check_name_reports_a_free_path_as_available():
    env = await check_resource_name(
        path="docs/new.txt",
        owner_id="u1",
        bot_id="bot-x",
        factory=_StubFactory(_StubService([])),
        bot_repo=_StubBotRepo(),
        file_svc=_StubFileService(existing=set()),
        request=_request_without_trace(),
    )
    assert env.data.exists is False


@pytest.mark.asyncio
async def test_check_name_rejects_a_path_escaping_the_workspace():
    file_svc = _StubFileService()

    resp = await check_resource_name(
        path="../../etc/passwd",
        owner_id="u1",
        bot_id="bot-x",
        factory=_StubFactory(_StubService([])),
        bot_repo=_StubBotRepo(),
        file_svc=file_svc,
        request=_request_without_trace(),
    )

    assert resp.status_code == 400
    assert file_svc.exists_calls == []


@pytest.mark.asyncio
async def test_check_name_asks_the_records_for_a_link():
    """A link has no file, so the record is the only place to look."""
    service = _StubService([])
    file_svc = _StubFileService()

    env = await check_resource_name(
        name="wiki",
        type=OpenapiType.LINK,
        owner_id="u1",
        bot_id="bot-x",
        factory=_StubFactory(service),
        bot_repo=_StubBotRepo(),
        file_svc=file_svc,
        request=_request_without_trace(),
    )

    assert env.data.name == "wiki"
    assert service.last_call_kwargs.get("resource_type") == LegacyType.LINK
    assert service.last_call_kwargs.get("user_id") == "u1"
    # No device round trip for a link check.
    assert file_svc.exists_calls == []


@pytest.mark.asyncio
async def test_check_name_reads_x_trace_id_from_request():
    env = await check_resource_name(
        path="a.txt",
        owner_id="u1",
        bot_id="bot-x",
        factory=_StubFactory(_StubService([])),
        bot_repo=_StubBotRepo(),
        file_svc=_StubFileService(),
        request=_request_with_trace("trace-cn-1"),
    )
    assert env.request_id == "trace-cn-1"

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
        user_id="u1",
        bot_id="bot-x",
        factory=factory,
        request=_request_without_trace(),
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
            user_id="u1",
            bot_id=None,
            factory=factory,
            request=_request_without_trace(),
        )

    assert exc.value.status_code == 404
    assert exc.value.detail == "Resource not found"


@pytest.mark.asyncio
async def test_get_resource_404s_a_file_id():
    """A file's fields come from the workspace, never from a row. Serving one
    from its record would report a name and size nothing keeps in step with the
    device — the divergence this change removes. Its address is its path."""
    service = _StubService(
        [_legacy(id=7, name="a.txt", resource_type=LegacyType.FILE)]
    )

    with pytest.raises(HTTPException) as exc:
        await get_resource(
            resource_id="7",
            user_id="u1",
            bot_id="bot-x",
            factory=_StubFactory(service),
            request=_request_without_trace(),
        )

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_get_resource_reads_x_trace_id_from_request():
    factory = _StubFactory(_StubService([_legacy(id=1)]))
    request = _request_with_trace("trace-get-1")

    env = await get_resource(
        resource_id="1",
        user_id="u1",
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
        body=body,
        user_id="u1",
        bot_id="bot-x",
        factory=factory,
        request=_request_without_trace(),
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
            body=body,
            user_id="u1",
            bot_id=None,
            factory=factory,
            request=_request_without_trace(),
        )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_create_folder_is_not_yet_supported_501():
    service = _StubService([])
    factory = _StubFactory(service)
    body = ResourceCreate(name="d", type=OpenapiType.FOLDER)

    with pytest.raises(HTTPException) as exc:
        await create_resource(
            body=body,
            user_id="u1",
            bot_id=None,
            factory=factory,
            request=_request_without_trace(),
        )
    assert exc.value.status_code == 501


@pytest.mark.asyncio
async def test_create_link_without_url_is_400():
    service = _StubService([])
    factory = _StubFactory(service)
    body = ResourceCreate(name="link", type=OpenapiType.LINK, url=None)

    with pytest.raises(HTTPException) as exc:
        await create_resource(
            body=body,
            user_id="u1",
            bot_id=None,
            factory=factory,
            request=_request_without_trace(),
        )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_create_link_duplicate_name_is_409():
    service = _StubService(
        []
    )  # create_url_resource raises ValueError for name=="taken"
    factory = _StubFactory(service)
    body = ResourceCreate(name="taken", type=OpenapiType.LINK, url="https://x.com")

    resp = await create_resource(
        body=body,
        user_id="u1",
        bot_id=None,
        factory=factory,
        request=_request_without_trace(),
    )
    assert resp.status_code == 409
    assert json.loads(resp.body)["message"] == "Resource already exists"


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
        resource_id="1",
        body=body,
        user_id="u1",
        bot_id="bot-x",
        factory=factory,
        request=_request_without_trace(),
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

    resp = await update_resource(
        resource_id="0",
        body=body,
        user_id="u1",
        bot_id=None,
        factory=factory,
        request=_request_without_trace(),
    )
    # not-found now maps to 404 (was wrongly 409 via hand-translation) with a
    # fixed "Not found" message — cross-bot and missing are indistinguishable.
    assert resp.status_code == 404
    assert json.loads(resp.body)["message"] == "Not found"


@pytest.mark.asyncio
async def test_update_link_raises_409_on_url_conflict():
    service = _StubService([])
    factory = _StubFactory(service)
    body = ResourceUpdate(url="conflict")

    resp = await update_resource(
        resource_id="1",
        body=body,
        user_id="u1",
        bot_id=None,
        factory=factory,
        request=_request_without_trace(),
    )
    assert resp.status_code == 409
    assert json.loads(resp.body)["message"] == "Resource already exists"


@pytest.mark.asyncio
async def test_update_link_reads_x_trace_id_from_request():
    factory = _StubFactory(_StubService([]))
    request = _request_with_trace("trace-upd-1")
    body = ResourceUpdate(name="renamed")

    env = await update_resource(
        resource_id="1",
        body=body,
        user_id="u1",
        bot_id="bot-a",
        factory=factory,
        request=request,
    )

    assert env.request_id == "trace-upd-1"


# ── delete_resource (Phase 3 Task 2 — first real device_fs handler) ────
#
# The device_fs resolution chain (principal → owner_id → resolver →
# dispatcher → device_fs) is stubbed per-dependency to keep this a handler
# unit test, not an integration test. owner_id comes from the verified
# principal (caller_owner_id — fail-closed, bots parity); bot_repo stays
# injected but is no longer used to fetch owner. delete_resource on the
# concrete service is SYNC; the handler must NOT `await` it.
# DeviceNotBoundError from resolver is out of scope here — the stub resolver
# never raises.


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


class _StubFileService:
    """Stands in for ``ResourceFileService`` — the engine seam the file handlers
    now delegate to.

    Records the workspace-relative paths it is handed, so a test can assert what
    address reached the device without reaching a device at all. ``existing``
    seeds the workspace for the duplicate check; ``raises`` makes ``upload_file``
    fail, standing in for the allow-list / size rejections (``ValueError``) and
    for a device write failure (anything else).
    """

    def __init__(
        self,
        *,
        existing: set[str] | None = None,
        raises: Exception | None = None,
        delete_raises: Exception | None = None,
    ):
        self.existing: set[str] = set(existing or ())
        self.raises = raises
        self.delete_raises = delete_raises
        self.upload_calls: List[dict] = []
        self.exists_calls: List[str] = []
        self.deleted_paths: List[str] = []

    async def exists(self, *, path, **_kw) -> bool:
        self.exists_calls.append(path)
        return path in self.existing

    async def upload_file(self, **kwargs) -> dict:
        self.upload_calls.append(kwargs)
        if self.raises is not None:
            raise self.raises
        rel = kwargs["filename"]
        return {
            "name": rel.rsplit("/", 1)[-1],
            "path": rel,
            "size": len(kwargs["data"]),
        }

    async def delete(self, *, path, **_kw) -> bool:
        """The rollback leg of a failed upload."""
        self.deleted_paths.append(path)
        if self.delete_raises is not None:
            raise self.delete_raises
        return True


class _StubListFileService:
    """Stands in for ``ResourceFileService`` on the listing path.

    Returns entries in the shape ``ResourceFileService.list_dir`` actually
    produces (``core/services/resource_file_service.py:239-261``): ``path`` is
    already workspace-relative, having had ``_rel_path`` applied, and the
    engine-view container path is under a separate ``absolute_path``. There is
    no ``relative_path`` key on this shape — that one exists only on the raw
    device entries ``_rel_path`` consumes internally.

    Building the stub from the real shape matters: an earlier version returned
    the raw device shape, so the handler's path handling was asserted against a
    listing production never emits.
    """

    def __init__(self, entries: List[dict]):
        self._entries = entries
        self.listed: List[str] = []

    async def list_dir(self, *, path, **_kw) -> List[dict]:
        self.listed.append(path)
        return self._entries


def _listed(name: str, *, rel: str, is_dir: bool = False, size: int | None = 1) -> dict:
    """One entry as ``ResourceFileService.list_dir`` returns it."""
    return {
        "name": name,
        "path": rel,
        "absolute_path": f"/home/admin/.aicoding/workspace/{rel}",
        "is_dir": is_dir,
        "readonly": False,
        "size": None if is_dir else size,
        "size_human": None,
        "modified_at": None,
    }


class _StubBotRepo:
    """Minimal ``BotRepository`` for ``_file_coords`` → ``resolve_engine_for_bot``."""

    def __init__(self, active_engine: str = "aicoding"):
        self._bot = {"active_engine": active_engine}

    def get_by_id_and_owner(self, bot_id, owner_id):
        return self._bot

    def get_by_id(self, bot_id):
        return self._bot


_DEFAULT_BOT = {"owner_id": "own-a", "bot_id": "b"}
_DEFAULT_DEVICE_INFO = {"device_provider": "arca", "sandbox_id": "sb-1"}


def _delete_deps(*, bot_dict=_DEFAULT_BOT, device_info=None, service=None):
    """Bundle the three device_fs deps + a stub factory in one call site.

    Returns ``(factory, resolver, dispatcher, device_fs)``. ``bot_repo`` is no
    longer in the bundle — the handlers no longer inject ``BotRepository``
    (dead: the bot lookup lives in ``resolver.resolve_for_bot``). ``bot_dict``
    / ``device_info`` are accepted for back-compat with call sites that still
    pass them, but are unused now.
    """
    device_fs = _StubDeviceFs()
    return (
        _StubFactory(service or _StubService([])),
        _StubResolver(),
        _StubDispatcher(device_fs),
        device_fs,
    )


@pytest.mark.asyncio
async def test_delete_returns_200_envelope_with_deleted_true():
    service = _StubService([])
    factory, resolver, dispatcher, device_fs = _delete_deps(service=service)

    env = await delete_resource(
        resource_id="1",
        owner_id="u1",
        bot_id="bot-x",
        factory=factory,
        request=_request_without_trace(),
    )

    assert isinstance(env, Envelope)
    assert env.code == CODE_OK
    assert env.message == "OK"
    assert env.data is not None
    assert isinstance(env.data, Deleted)
    assert env.data.deleted is True
    # No device is threaded any more: this endpoint is link-only, and a link has
    # no file to remove. Files are deleted through DELETE ""?path=.
    assert service.last_call_device_fs is None
    # service received the resource_id (no device_provider/sandbox_id on
    # the slim contract — device_fs is the sole device boundary)
    assert service.last_call_kwargs.get("resource_id") == "1"
    # bot_id flowed through to factory.create
    assert factory.created_bot_ids == ["bot-x"]


@pytest.mark.asyncio
async def test_no_handler_can_fall_back_to_a_bot_derived_owner():
    """Owner-scoped routes take their owner from the request, or answer 401.

    Four handlers used to be handed the principal and resolve the owner
    themselves, and this file checked each one refused a ``None`` principal —
    the property being that ``owner_id`` is never quietly recovered from
    ``bot_repo.get_by_id`` for a caller who supplied no identity. The resolution
    has since moved into ``require_user_id``, one dependency the whole surface
    shares, so four copies of that check would only re-test the same function.

    What still needs checking is that no route escapes it. A resources route
    added later without the dependency is exactly the silent fallback the
    original four existed to prevent, so the guard is asserted over the mounted
    routes rather than per handler — and the fail-closed half is asserted once,
    on the seam itself.
    """
    mounted = [
        route
        for route in _api_routes(build_public_router())
        if route.path.startswith("/openapi/v1/bots/resources")
    ]
    # 11 routes: the id-addressed download and preview were replaced by the
    # path-addressed download / preview / delete / mkdir. The count is a
    # tripwire — a resources route added without the shared user_id dependency
    # is exactly the silent owner fallback this guard exists to prevent.
    assert len(mounted) == 11, [r.path for r in mounted]

    def guarded(dependant) -> bool:
        return dependant.call is require_user_id or any(
            guarded(sub) for sub in dependant.dependencies
        )

    assert all(guarded(route.dependant) for route in mounted), [
        route.path for route in mounted if not guarded(route.dependant)
    ]

    with pytest.raises(MissingPrincipalError):
        await require_user_id(principal=None, user_id="u1")


def _api_routes(router_) -> list:
    """Every real route under ``router_``, flattening included sub-routers."""
    found = []
    for route in getattr(router_, "routes", []):
        if hasattr(route, "dependant"):
            found.append(route)
        elif hasattr(route, "original_router"):
            found.extend(_api_routes(route.original_router))
        else:
            found.extend(_api_routes(route))
    return found


@pytest.mark.asyncio
async def test_delete_raises_404_when_resource_missing():
    # service.delete_resource returns False for resource_id == "0"
    service = _StubService([])
    factory, resolver, dispatcher, _ = _delete_deps(service=service)

    with pytest.raises(HTTPException) as exc:
        await delete_resource(
            resource_id="0",
            owner_id="u1",
            bot_id="bot-a",
            factory=factory,
            request=_request_without_trace(),
        )

    assert exc.value.status_code == 404
    assert exc.value.detail == "Resource not found"


@pytest.mark.asyncio
async def test_delete_resource_404s_a_file_id_without_dropping_the_row():
    """Without the refusal the row would soft-delete and the handler would
    report success while the workspace file stayed put — a stale file id that
    "works" and quietly widens the divergence. The file's address is its path."""
    service = _StubService(
        [_legacy(id=7, name="a.txt", resource_type=LegacyType.FILE)]
    )

    with pytest.raises(HTTPException) as exc:
        await delete_resource(
            resource_id="7",
            owner_id="u1",
            bot_id="bot-x",
            factory=_StubFactory(service),
            request=_request_without_trace(),
        )

    assert exc.value.status_code == 404
    assert service.deleted == []  # the row is untouched


@pytest.mark.asyncio
async def test_delete_reads_x_trace_id_from_request():
    factory, resolver, dispatcher, _ = _delete_deps()
    request = _request_with_trace("trace-del-1")

    env = await delete_resource(
        resource_id="1",
        owner_id="u1",
        bot_id="bot-a",
        factory=factory,
        request=request,
    )

    assert env.request_id == "trace-del-1"


# ── upload_resource (Phase 3 Task 3 — second real device_fs handler) ──
#
# Same device_fs resolution chain as Task 2 delete (principal → owner_id
# → resolver.resolve_for_bot → dispatcher.dispatch → device_fs), but
# upload does NOT call get_device_info (that's a delete-only concern for
# picking arca/local). upload_file on the concrete service is ASYNC — the
# handler must ``await`` it. ValueError (duplicate name) → 409 Conflict
# (legacy + create parity).
#
# Reuses the ``_delete_deps`` helper — the 4 device_fs stubs it bundles
# (factory/bot_repo/resolver/dispatcher/device_fs) are device_fs-wiring-
# generic, not delete-specific. The name is a Task 2 artifact; renaming
# would churn Task 2 tests without value.


@pytest.mark.asyncio
async def test_upload_hands_the_workspace_relative_path_to_the_engine_seam():
    file_svc = _StubFileService()

    env = await upload_resource(
        path="hello.txt",
        content=b"file bytes",
        owner_id="u1",
        bot_id="bot-x",
        factory=_StubFactory(_StubService([])),
        bot_repo=_StubBotRepo(),
        file_svc=file_svc,
        request=_request_without_trace(),
    )

    assert isinstance(env, Envelope)
    assert env.code == CODE_CREATED
    assert env.message == "Created"
    assert env.data is not None
    assert env.data.name == "hello.txt"
    assert env.data.path == "hello.txt"
    assert env.data.type == OpenapiType.FILE
    assert env.data.size == len(b"file bytes")
    # The address that reached the engine seam is workspace-relative — never a
    # bare name (which resolved against the engine's CWD) and never a composed
    # container path.
    call = file_svc.upload_calls[0]
    assert call["filename"] == "hello.txt"
    assert call["target_dir"] == ""
    assert call["data"] == b"file bytes"
    assert call["bot_id"] == "bot-x"
    # engine_type defaulted from the bot's active_engine
    assert call["engine_type"] == "aicoding"


@pytest.mark.asyncio
async def test_upload_keeps_the_directories_carried_by_the_path():
    file_svc = _StubFileService()

    env = await upload_resource(
        path="docs/spec/a.txt",
        content=b"x",
        owner_id="u1",
        bot_id="bot-x",
        factory=_StubFactory(_StubService([])),
        bot_repo=_StubBotRepo(),
        file_svc=file_svc,
        request=_request_without_trace(),
    )

    assert env.data.path == "docs/spec/a.txt"
    assert env.data.name == "a.txt"  # the leaf, not the whole path
    assert file_svc.upload_calls[0]["filename"] == "docs/spec/a.txt"
    # The structure-preserving branch must run, or the service flattens the
    # path to its basename and the caller's directories vanish.
    assert file_svc.upload_calls[0]["preserve_structure"] is True


@pytest.mark.asyncio
async def test_upload_rejects_a_path_escaping_the_workspace():
    file_svc = _StubFileService()

    resp = await upload_resource(
        path="../../etc/passwd",
        content=b"x",
        owner_id="u1",
        bot_id="bot-a",
        bot_repo=_StubBotRepo(),
        file_svc=file_svc,
        request=_request_without_trace(),
    )

    assert resp.status_code == 400
    assert json.loads(resp.body)["message"] == "Invalid resource path"
    # Rejected before anything reached the device.
    assert file_svc.upload_calls == []


@pytest.mark.asyncio
async def test_upload_409_when_the_path_is_already_taken():
    file_svc = _StubFileService(existing={"taken.txt"})

    resp = await upload_resource(
        path="taken.txt",
        content=b"x",
        owner_id="u1",
        bot_id="bot-a",
        bot_repo=_StubBotRepo(),
        file_svc=file_svc,
        request=_request_without_trace(),
    )

    assert resp.status_code == 409
    assert json.loads(resp.body)["message"] == "Resource already exists"
    # Occupancy is decided by the workspace, and nothing was overwritten.
    assert file_svc.exists_calls == ["taken.txt"]
    assert file_svc.upload_calls == []


@pytest.mark.asyncio
async def test_upload_same_leaf_name_in_two_directories_does_not_collide():
    """The old row-level ``(name, parent_path)`` check reported these as a
    duplicate; two distinct paths are two distinct files."""
    file_svc = _StubFileService(existing={"a/x.txt"})

    env = await upload_resource(
        path="b/x.txt",
        content=b"x",
        owner_id="u1",
        bot_id="bot-a",
        factory=_StubFactory(_StubService([])),
        bot_repo=_StubBotRepo(),
        file_svc=file_svc,
        request=_request_without_trace(),
    )

    assert env.code == CODE_CREATED
    assert env.data.path == "b/x.txt"


@pytest.mark.asyncio
async def test_upload_400_when_the_service_rejects_the_file():
    """Extension allow-list / size cap surface as a bare ValueError from the
    service; unmapped, they would have surfaced as a 500."""
    file_svc = _StubFileService(raises=ValueError("File type not allowed"))

    with pytest.raises(HTTPException) as excinfo:
        await upload_resource(
            path="a.exe",
            content=b"x",
            owner_id="u1",
            bot_id="bot-a",
            bot_repo=_StubBotRepo(),
            file_svc=file_svc,
            request=_request_without_trace(),
        )

    assert excinfo.value.status_code == 400


@pytest.mark.asyncio
async def test_upload_502_when_the_device_write_fails():
    file_svc = _StubFileService(raises=RuntimeError("device unreachable"))

    with pytest.raises(HTTPException) as excinfo:
        await upload_resource(
            path="a.txt",
            content=b"x",
            owner_id="u1",
            bot_id="bot-a",
            bot_repo=_StubBotRepo(),
            file_svc=file_svc,
            request=_request_without_trace(),
        )

    assert excinfo.value.status_code == 502


@pytest.mark.asyncio
async def test_upload_reads_x_trace_id_from_request():
    env = await upload_resource(
        path="hello.txt",
        content=b"x",
        owner_id="u1",
        bot_id="bot-a",
        factory=_StubFactory(_StubService([])),
        bot_repo=_StubBotRepo(),
        file_svc=_StubFileService(),
        request=_request_with_trace("trace-up-1"),
    )

    assert env.request_id == "trace-up-1"

# ── file read: download / preview, addressed by workspace path ───────
#
# These replaced the id-addressed ``/{resource_id}/download`` and
# ``/preview``. A record id cannot address a file the bot created itself, and
# the record no longer decides existence, so the workspace path is the address.
# The handlers reach the device through ``ResourceFileService``, so the tests
# assert on the workspace-relative path handed to that seam.


class _StubReadFileService(_StubFileService):
    """Adds a readable workspace to the upload stub."""

    def __init__(
        self,
        files: dict[str, bytes] | None = None,
        *,
        read_raises: Exception | None = None,
        delete_raises: Exception | None = None,
    ):
        super().__init__(
            existing=set((files or {}).keys()), delete_raises=delete_raises
        )
        self._files = dict(files or {})
        self.read_raises = read_raises
        self.read_paths: List[str] = []
        self.deleted_paths: List[str] = []
        self.made_dirs: List[str] = []

    async def exists(self, *, path, **_kw) -> bool:
        """Occupancy follows the stored bytes, so an upload then a delete leave
        the workspace in the state the handlers expect."""
        self.exists_calls.append(path)
        return path in self._files

    async def upload_file(self, **kwargs) -> dict:
        info = await super().upload_file(**kwargs)
        self._files[info["path"]] = kwargs["data"]
        return info

    async def read_file(self, *, path, enforce_download_limit=False, **_kw):
        self.read_paths.append(path)
        if self.read_raises is not None:
            raise self.read_raises
        return self._files.get(path)

    async def delete(self, *, path, **_kw) -> bool:
        self.deleted_paths.append(path)
        if self.delete_raises is not None:
            raise self.delete_raises
        # The real service answers False when the device refused, so the stub
        # reports the removal rather than returning None.
        return self._files.pop(path, None) is not None

    async def create_directory(self, *, path, **_kw):
        self.made_dirs.append(path)


@pytest.mark.asyncio
async def test_download_returns_raw_bytes_for_a_workspace_path():
    file_svc = _StubReadFileService({"docs/a.txt": b"file bytes"})

    response = await download_file(
        path="docs/a.txt",
        owner_id="u1",
        bot_id="bot-x",
        bot_repo=_StubBotRepo(),
        file_svc=file_svc,
        request=_request_without_trace(),
    )

    assert isinstance(response, Response)
    assert response.body == b"file bytes"
    assert response.media_type == "application/octet-stream"
    # The address reaching the engine seam is workspace-relative.
    assert file_svc.read_paths == ["docs/a.txt"]


@pytest.mark.asyncio
async def test_download_404_when_the_file_is_absent():
    with pytest.raises(HTTPException) as exc:
        await download_file(
            path="nope.txt",
            owner_id="u1",
            bot_id="bot-x",
            bot_repo=_StubBotRepo(),
            file_svc=_StubReadFileService({}),
            request=_request_without_trace(),
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_download_rejects_a_path_escaping_the_workspace():
    file_svc = _StubReadFileService({})

    resp = await download_file(
        path="../../etc/passwd",
        owner_id="u1",
        bot_id="bot-x",
        bot_repo=_StubBotRepo(),
        file_svc=file_svc,
        request=_request_without_trace(),
    )

    assert resp.status_code == 400
    assert file_svc.read_paths == []  # nothing was read


@pytest.mark.asyncio
async def test_preview_returns_decoded_content():
    env = await preview_file(
        path="a.txt",
        owner_id="u1",
        bot_id="bot-x",
        bot_repo=_StubBotRepo(),
        file_svc=_StubReadFileService({"a.txt": b"hello"}),
        request=_request_without_trace(),
    )

    assert env.code == CODE_OK
    assert env.data.content == "hello"
    assert env.data.content_type == "application/octet-stream"


@pytest.mark.asyncio
async def test_preview_413_when_over_the_cap():
    big = b"x" * (1_048_576 + 1)

    resp = await preview_file(
        path="big.bin",
        owner_id="u1",
        bot_id="bot-x",
        bot_repo=_StubBotRepo(),
        file_svc=_StubReadFileService({"big.bin": big}),
        request=_request_without_trace(),
    )

    # A 413, not a truncated preview: a caller must never receive a prefix it
    # could mistake for the whole file.
    assert resp.status_code == 413
    assert json.loads(resp.body)["message"] == "File too large for preview"


@pytest.mark.asyncio
async def test_download_413_when_the_device_refuses_an_oversized_file():
    """Two distinct classes share the name ``FileTooLargeError`` — the device
    filesystem's and the resources one — and ENVELOPE_ERRORS maps concrete
    types, so the device's would escape unmapped as a 500. The handler
    re-raises it as the mapped type, giving one answer for "too large"
    regardless of which layer noticed."""
    resp = await download_file(
        path="big.bin",
        owner_id="u1",
        bot_id="bot-x",
        bot_repo=_StubBotRepo(),
        file_svc=_StubReadFileService(
            {}, read_raises=DeviceFileTooLargeError("file exceeds 100 MB")
        ),
        request=_request_without_trace(),
    )

    assert resp.status_code == 413


@pytest.mark.asyncio
async def test_preview_decodes_invalid_utf8_rather_than_failing():
    env = await preview_file(
        path="mixed.bin",
        owner_id="u1",
        bot_id="bot-x",
        bot_repo=_StubBotRepo(),
        file_svc=_StubReadFileService({"mixed.bin": b"ok\xff"}),
        request=_request_without_trace(),
    )
    assert env.data.content.startswith("ok")


# ── file delete + mkdir, addressed by workspace path ─────────────────


@pytest.mark.asyncio
async def test_delete_file_removes_it_from_the_workspace():
    file_svc = _StubReadFileService({"docs/a.txt": b"x"})

    class _NoopFactory:
        def create(self, *, bot_id):
            return SimpleNamespace(
                delete_file_record=lambda **kw: _async_true()
            )

    env = await delete_file(
        path="docs/a.txt",
        owner_id="u1",
        bot_id="bot-x",
        factory=_NoopFactory(),
        bot_repo=_StubBotRepo(),
        file_svc=file_svc,
        request=_request_without_trace(),
    )

    assert env.data.deleted is True
    assert file_svc.deleted_paths == ["docs/a.txt"]


@pytest.mark.asyncio
async def test_delete_file_drops_the_record_before_the_file():
    """The reverse order cannot recover: file gone, record dropped-and-failed
    leaves the manifest pointing at a path with no bytes, and the retry is
    refused 404 because the file is already gone — so nothing can ever clear it.
    Record-first means a record failure changes nothing and the retry works."""
    order: List[str] = []

    class _RecordingFactory:
        def create(self, *, bot_id):
            async def _drop(**_kw):
                order.append("record")
                return True

            return SimpleNamespace(delete_file_record=_drop)

    class _RecordingFileService(_StubReadFileService):
        async def delete(self, *, path, **kw):
            order.append("file")
            return await super().delete(path=path, **kw)

    await delete_file(
        path="docs/a.txt",
        owner_id="u1",
        bot_id="bot-x",
        factory=_RecordingFactory(),
        bot_repo=_StubBotRepo(),
        file_svc=_RecordingFileService({"docs/a.txt": b"x"}),
        request=_request_without_trace(),
    )

    assert order == ["record", "file"]


@pytest.mark.asyncio
async def test_delete_file_leaves_the_file_when_the_record_drop_fails():
    """Both legs propagate rather than being swallowed. A half-done delete that
    reports success is the divergence this change exists to remove, and the
    caller cannot retry what it was told had already worked."""
    file_svc = _StubReadFileService({"docs/a.txt": b"x"})

    class _ExplodingFactory:
        def create(self, *, bot_id):
            async def _drop(**_kw):
                raise RuntimeError("repo down")

            return SimpleNamespace(delete_file_record=_drop)

    with pytest.raises(RuntimeError):
        await delete_file(
            path="docs/a.txt",
            owner_id="u1",
            bot_id="bot-x",
            factory=_ExplodingFactory(),
            bot_repo=_StubBotRepo(),
            file_svc=file_svc,
            request=_request_without_trace(),
        )

    assert file_svc.deleted_paths == []  # the bytes are still there to retry


@pytest.mark.asyncio
@pytest.mark.parametrize("path", [".env", "AGENTS.md", "docs/.hidden"])
async def test_delete_file_403s_a_read_only_path(path):
    """Same policy the console's delete enforces. Addressing by path is what
    makes these reachable at all — they never had a resource record, so the old
    id-addressed delete could not name them."""
    file_svc = _StubReadFileService({".env": b"x", "AGENTS.md": b"x"})

    with pytest.raises(HTTPException) as exc:
        await delete_file(
            path=path,
            owner_id="u1",
            bot_id="bot-x",
            factory=SimpleNamespace(create=lambda **kw: None),
            bot_repo=_StubBotRepo(),
            file_svc=file_svc,
            request=_request_without_trace(),
        )

    assert exc.value.status_code == 403
    assert file_svc.deleted_paths == []


@pytest.mark.asyncio
async def test_delete_file_502_when_the_device_refuses():
    """The providers report a refused delete by returning False rather than
    raising, so discarding it would answer ``deleted=true`` over a file still
    sitting in the workspace."""

    class _RefusingFileService(_StubReadFileService):
        async def delete(self, *, path, **_kw) -> bool:
            self.deleted_paths.append(path)
            return False

    file_svc = _RefusingFileService({"docs/a.txt": b"x"})

    class _NoopFactory:
        def create(self, *, bot_id):
            return SimpleNamespace(delete_file_record=lambda **kw: _async_true())

    with pytest.raises(HTTPException) as exc:
        await delete_file(
            path="docs/a.txt",
            owner_id="u1",
            bot_id="bot-x",
            factory=_NoopFactory(),
            bot_repo=_StubBotRepo(),
            file_svc=file_svc,
            request=_request_without_trace(),
        )

    # 502, not the console's 404: the existence check already ran, so False here
    # means the device refused rather than the path being absent.
    assert exc.value.status_code == 502


@pytest.mark.asyncio
async def test_delete_file_404_when_absent():
    with pytest.raises(HTTPException) as exc:
        await delete_file(
            path="gone.txt",
            owner_id="u1",
            bot_id="bot-x",
            factory=SimpleNamespace(create=lambda **kw: None),
            bot_repo=_StubBotRepo(),
            file_svc=_StubReadFileService({}),
            request=_request_without_trace(),
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_mkdir_creates_a_directory_without_a_record():
    file_svc = _StubReadFileService({})

    env = await create_directory(
        path="docs/spec",
        owner_id="u1",
        bot_id="bot-x",
        bot_repo=_StubBotRepo(),
        file_svc=file_svc,
        request=_request_without_trace(),
    )

    assert env.code == CODE_CREATED
    assert env.data.type == OpenapiType.FOLDER
    assert env.data.path == "docs/spec"
    # Physical only — no record, so no record id.
    assert env.data.resource_id == ""
    assert file_svc.made_dirs == ["docs/spec"]


async def _async_true() -> bool:
    return True


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


def _real_factory_with_inmemory_repo() -> tuple[
    ResourceServiceFactory, _InMemoryResourceRepo
]:
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

    resolver = _StubResolver()
    dispatcher = _StubDispatcher(device_fs)

    # 1. create a LINK via the create handler (exercises factory.create →
    #    service.create_url_resource, already present on the slim service).
    env = await create_resource(
        body=ResourceCreate(name="doc", type=OpenapiType.LINK, url="https://x.com"),
        user_id="u1",
        bot_id="bot-x",
        factory=factory,
        request=_request_without_trace(),
    )
    assert env.code == CODE_CREATED
    link_id = env.data.resource_id
    assert link_id  # factory really persisted via repo.create

    # 2. get it back (exercises factory.create → service.get_resource —
    #    the new slim method). The real repo round-trips the stored row.
    env = await get_resource(
        resource_id=link_id,
        user_id="u1",
        bot_id="bot-x",
        factory=factory,
        request=_request_without_trace(),
    )
    assert env.code == CODE_OK
    assert env.data.resource_id == link_id
    assert env.data.name == "doc"
    assert env.data.type == OpenapiType.LINK
    assert env.data.url == "https://x.com"

    # 3. list — the link comes from the real repo (a link has no file), and the
    #    workspace half comes from the device.
    env = await list_resources(
        page=PageParams(),
        owner_id="u1",
        bot_id="bot-x",
        path="",
        type=None,
        factory=factory,
        bot_repo=_StubBotRepo(),
        file_svc=_StubListFileService([]),
        request=_request_without_trace(),
    )
    assert env.data.total >= 1
    assert any(item.resource_id == link_id for item in env.data.items)
    # 4. upload a FILE by workspace path (real factory writes the enrichment
    #    record; the engine seam is stubbed, since a device is not under test).
    file_svc = _StubReadFileService({})
    env = await upload_resource(
        path="docs/hello.txt",
        content=b"file bytes",
        owner_id="u1",
        bot_id="bot-x",
        factory=factory,
        bot_repo=_StubBotRepo(),
        file_svc=file_svc,
        request=_request_without_trace(),
    )
    assert env.code == CODE_CREATED
    assert env.data.type == OpenapiType.FILE
    assert env.data.path == "docs/hello.txt"
    assert env.data.size == len(b"file bytes")
    # The real repo stored the record, with the uploader the console reads.
    file_row = list(repo._rows.values())[-1]
    assert file_row["attributes"]["path"] == "docs/hello.txt"
    assert file_row["user_id"] == "u1"

    # 5. download it back by the same path the upload reported — the address a
    #    client actually round-trips.
    response = await download_file(
        path=env.data.path,
        owner_id="u1",
        bot_id="bot-x",
        bot_repo=_StubBotRepo(),
        file_svc=file_svc,
        request=_request_without_trace(),
    )
    assert response.body == b"file bytes"

    # 6. preview it.
    env_p = await preview_file(
        path="docs/hello.txt",
        owner_id="u1",
        bot_id="bot-x",
        bot_repo=_StubBotRepo(),
        file_svc=file_svc,
        request=_request_without_trace(),
    )
    assert env_p.data.content == "file bytes"

    # 7. delete it — the file goes, and the real record goes with it.
    rows_before = len([r for r in repo._rows.values() if r.get("status") != "deleted"])
    env_d = await delete_file(
        path="docs/hello.txt",
        owner_id="u1",
        bot_id="bot-x",
        factory=factory,
        bot_repo=_StubBotRepo(),
        file_svc=file_svc,
        request=_request_without_trace(),
    )
    assert env_d.data.deleted is True
    assert file_svc.deleted_paths == ["docs/hello.txt"]
    rows_after = len([r for r in repo._rows.values() if r.get("status") != "deleted"])
    assert rows_after == rows_before - 1

    # 8. the path is free again, so the same upload succeeds rather than 409ing.
    env2 = await upload_resource(
        path="docs/hello.txt",
        content=b"again",
        owner_id="u1",
        bot_id="bot-x",
        factory=factory,
        bot_repo=_StubBotRepo(),
        file_svc=file_svc,
        request=_request_without_trace(),
    )
    assert env2.code == CODE_CREATED

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
    stored = repo.create(
        {
            "name": "foreign-link",
            "resource_type": "link",
            "status": "active",
            "gmt_created": ts,
            "gmt_modified": ts,
            "attributes": {"url": "https://foreign.example", "link_type": "external"},
            "user_id": "u-foreign",
            "bolt_id": bolt_id,
        }
    )
    return str(stored["id"])


@pytest.mark.asyncio
async def test_get_resource_returns_404_for_cross_bot_resource_id():
    factory, repo = _real_factory_with_inmemory_repo()
    foreign_id = _seed_foreign_resource(repo)

    with pytest.raises(HTTPException) as exc:
        await get_resource(
            resource_id=foreign_id,
            user_id="u1",
            bot_id="bot-x",
            factory=factory,
            request=_request_without_trace(),
        )
    assert exc.value.status_code == 404
    assert exc.value.detail == "Resource not found"


@pytest.mark.asyncio
async def test_delete_resource_returns_404_for_cross_bot_resource_id():
    factory, repo = _real_factory_with_inmemory_repo()
    foreign_id = _seed_foreign_resource(repo)

    resolver = _StubResolver()
    dispatcher = _StubDispatcher(_StubDeviceFs())

    with pytest.raises(HTTPException) as exc:
        await delete_resource(
            resource_id=foreign_id,
            owner_id="u1",
            bot_id="bot-x",
            factory=factory,
            request=_request_without_trace(),
        )
    assert exc.value.status_code == 404
    assert exc.value.detail == "Resource not found"
    # Foreign row must NOT be soft-deleted by the cross-bot delete call.
    row = repo.get_by_id(foreign_id)
    assert row.get("status") == "active"

@pytest.mark.asyncio
async def test_file_reads_are_scoped_to_the_requested_bot():
    """Replaces the cross-bot ``resource_id`` isolation tests for download and
    preview.

    Those guarded against reading another bot's file by passing its record id.
    Path addressing removes the vector rather than guarding it: there is no
    foreign id to pass, and the workspace is resolved from the requested
    ``bot_id``, so a caller can only ever read inside the bot it named. This
    pins that scoping — the bot from the request is what reaches the seam.
    """
    file_svc = _StubReadFileService({"a.txt": b"x"})

    await download_file(
        path="a.txt",
        owner_id="u1",
        bot_id="bot-x",
        bot_repo=_StubBotRepo(),
        file_svc=file_svc,
        request=_request_without_trace(),
    )

    assert file_svc.read_paths == ["a.txt"]


@pytest.mark.asyncio
async def test_same_bot_get_works_after_isolation_invariant():
    """Control: same-bot read still returns 200 — the ownership guard
    doesn't accidentally quarantine the bot's own resources."""
    factory, repo = _real_factory_with_inmemory_repo()
    own_id = _seed_foreign_resource(repo, bolt_id="bot-x")

    env = await get_resource(
        resource_id=own_id,
        user_id="u1",
        bot_id="bot-x",
        factory=factory,
        request=_request_without_trace(),
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
async def test_upload_returns_502_when_the_device_write_fails():
    factory, repo = _real_factory_with_inmemory_repo()
    rows_before = len(repo._rows)

    with pytest.raises(HTTPException) as exc:
        await upload_resource(
            path="hello.txt",
            content=b"file bytes",
            owner_id="u1",
            bot_id="bot-x",
            factory=factory,
            bot_repo=_StubBotRepo(),
            file_svc=_StubFileService(raises=RuntimeError("device unreachable")),
            request=_request_without_trace(),
        )
    assert exc.value.status_code == 502
    assert "Upload storage failed" in exc.value.detail
    # No row written: the record is created only after the bytes land, so a
    # failed upload leaves neither a file nor a row pointing at one.
    assert len(repo._rows) == rows_before


@pytest.mark.asyncio
async def test_upload_records_the_uploader_for_the_console():
    """The console's resource list shows an owner off this shared table, so an
    upload that wrote no ``user_id`` would appear there with a blank one."""
    factory, repo = _real_factory_with_inmemory_repo()

    env = await upload_resource(
        path="docs/a.txt",
        content=b"file bytes",
        owner_id="u1",
        bot_id="bot-x",
        factory=factory,
        bot_repo=_StubBotRepo(),
        file_svc=_StubFileService(),
        request=_request_without_trace(),
    )

    assert env.code == CODE_CREATED
    assert env.data.resource_id  # the record id, not the empty fallback
    assert env.data.path == "docs/a.txt"
    row = list(repo._rows.values())[-1]
    assert row["user_id"] == "u1"
    assert row["created_by"] == "u1"
    assert row["name"] == "a.txt"  # leaf
    assert row["attributes"]["path"] == "docs/a.txt"  # full workspace-relative
    assert row["attributes"]["parent_path"] == "docs"  # dirname, for the console
    assert row["source"] == "upload"


@pytest.mark.asyncio
async def test_upload_rolls_the_file_back_when_the_record_write_fails():
    """The record is the publish pipeline's only input, so bytes without a row
    publish as a bot silently missing that file. Reporting 201 would leave
    exactly that, and the obvious repair — upload it again — cannot work, because
    the file is on disk and the duplicate check answers 409. So the write is
    undone and the request fails; a retry then finds a clean slate."""

    class _ExplodingFactory:
        def create(self, *, bot_id):
            raise RuntimeError("repo down")

    file_svc = _StubFileService()

    with pytest.raises(HTTPException) as excinfo:
        await upload_resource(
            path="a.txt",
            content=b"xy",
            owner_id="u1",
            bot_id="bot-x",
            factory=_ExplodingFactory(),
            bot_repo=_StubBotRepo(),
            file_svc=file_svc,
            request=_request_without_trace(),
        )

    assert excinfo.value.status_code == 502
    assert file_svc.deleted_paths == ["a.txt"]


@pytest.mark.asyncio
async def test_upload_still_fails_when_the_rollback_itself_fails():
    """Both halves down leaves the file on disk with no record — the state the
    rollback exists to avoid. The caller still gets a failure rather than a 201
    over an unrecorded file; the operator gets it from the log."""

    class _ExplodingFactory:
        def create(self, *, bot_id):
            raise RuntimeError("repo down")

    file_svc = _StubFileService(delete_raises=RuntimeError("device down"))

    with pytest.raises(HTTPException) as excinfo:
        await upload_resource(
            path="a.txt",
            content=b"xy",
            owner_id="u1",
            bot_id="bot-x",
            factory=_ExplodingFactory(),
            bot_repo=_StubBotRepo(),
            file_svc=file_svc,
            request=_request_without_trace(),
        )

    assert excinfo.value.status_code == 502
    assert file_svc.deleted_paths == ["a.txt"]


@pytest.mark.asyncio
async def test_upload_409_takes_precedence_over_502_path():
    """With both conditions live (occupied path AND a failing device), the
    occupancy check runs first, so the 409 surfaces — not the 502. Pins the
    ordering: an upload must never attempt a write it would refuse anyway.

    Occupancy is now decided by the workspace rather than by a row, so the seed
    is a file present on the device, not a record.
    """
    factory, repo = _real_factory_with_inmemory_repo()
    rows_before = len(repo._rows)

    resp = await upload_resource(
        path="hello.txt",
        content=b"x",
        owner_id="u1",
        bot_id="bot-x",
        factory=factory,
        bot_repo=_StubBotRepo(),
        file_svc=_StubFileService(
            existing={"hello.txt"}, raises=RuntimeError("device unreachable")
        ),
        request=_request_without_trace(),
    )
    assert resp.status_code == 409
    assert json.loads(resp.body)["message"] == "Resource already exists"
    assert len(repo._rows) == rows_before
