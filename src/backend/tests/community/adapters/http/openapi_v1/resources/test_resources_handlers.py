"""openapi_v1 resources handler unit tests.

The group is files-only: every handler is addressed by a workspace-relative
``path``, and no response carries a record id. Tests that covered link
resources, the id-addressed get/update/delete, and ``check-name`` went with the
contract they were testing — ``stat`` answers the existence question those asked,
against the workspace rather than the record table.
"""

import io
import json
import logging
import os
import zipfile
from types import SimpleNamespace
from typing import List

import httpx
import pytest
from fastapi import HTTPException, Request, Response
from fastapi.responses import FileResponse

from tests.community.adapters.http.openapi_v1.conftest import public_router
from agentclaw.community.adapters.http.openapi_v1.errors import MissingPrincipalError
from agentclaw.community.adapters.http.openapi_v1.principal import require_user_id
from agentclaw.community.adapters.http.openapi_v1.contracts import (
    CODE_CREATED,
    CODE_OK,
    Envelope,
    PageParams,
)
from agentclaw.community.adapters.http.openapi_v1.resources.router import (
    create_directory,
    delete_file,
    download_directory,
    download_file,
    list_resources,
    preview_file,
    stat_resource,
    upload_resource,
)
from agentclaw.community.adapters.http.openapi_v1.resources.schemas import (
    FileEntry,
    ResourceType as OpenapiType,
)
from agentclaw.community.core.resources.factory import ResourceServiceFactory
from agentclaw.community.core.resources.service import (
    DirectoryTooLargeError,
    ResourceNotFoundError,
)
from agentclaw.community.core.devices.services.device_filesystem import (
    FileTooLargeError as DeviceFileTooLargeError,
)


def _request_scope() -> dict:
    """Minimal ASGI scope for a stubbed http request (no live server)."""
    return {
        "type": "http",
        "method": "GET",
        "headers": [],
        "path": "/",
        "query_string": b"",
    }


def _request_without_trace(query_string: bytes = b"") -> Request:
    """A request whose tracer middleware did not run — ``state.trace_id`` unset.

    ``responses._trace_id`` reads ``request.state.trace_id`` and falls back to
    ``""`` when absent, so the envelope's ``request_id`` is empty (mirrors the
    prod path before the tracer middleware stamps the id). A real
    ``fastapi.Request`` (not a ``SimpleNamespace``) so ``@envelope_errors``'
    ``_find_request`` recognises it on the error path.
    """
    scope = _request_scope()
    scope["query_string"] = query_string
    return Request(scope)


def _request_with_trace(trace_id: str) -> Request:
    """A request whose tracer middleware stamped ``trace_id`` on ``state``."""
    req = Request(_request_scope())
    req.state.trace_id = trace_id
    return req


def _http_status(code: int) -> httpx.HTTPStatusError:
    """The error a baas provider re-raises from the upstream file API."""
    request = httpx.Request("POST", "http://baas/api/file/read")
    return httpx.HTTPStatusError(
        f"HTTP {code}", request=request, response=httpx.Response(code, request=request)
    )


async def _async_true() -> bool:
    return True


# ── stubs ────────────────────────────────────────────────────────────
#
# Direct handler invocation, bypassing FastAPI's dependency wiring: handlers
# take a required ``request: Request`` (mirroring the bots router), whose
# ``state.trace_id`` is either unset (empty ``request_id``) or set to a known
# value asserted into the envelope.


class _StubService:
    """The two record methods the handlers still call.

    Nothing else: the record is the publish pipeline's input, and this API never
    reads one back. A stub with a ``get_resource`` or ``create_url_resource`` on
    it would suggest otherwise.
    """

    def __init__(self) -> None:
        self.recorded: List[dict] = []
        self.record_deletes: List[str] = []

    async def record_uploaded_file(
        self, *, path, size, user_id=None, created_by=None, source="upload"
    ):
        self.recorded.append(
            {"path": path, "size": size, "user_id": user_id, "created_by": created_by}
        )
        return SimpleNamespace(id="rec-1", name=path.rsplit("/", 1)[-1])

    async def delete_file_record(self, *, path) -> bool:
        self.record_deletes.append(path)
        return True


class _StubFactory:
    """Captures bot_id passed to create(); returns the configured service."""

    def __init__(self, service: _StubService | None = None) -> None:
        self._service = service or _StubService()
        self.created_bot_ids: list[str] = []

    def create(self, *, bot_id: str) -> _StubService:
        self.created_bot_ids.append(bot_id)
        return self._service


class _StubBotRepo:
    """Minimal ``BotRepository`` for ``_file_coords`` → ``resolve_engine_for_bot``."""

    def __init__(self, active_engine: str = "aicoding"):
        self._bot = {"active_engine": active_engine}

    def get_by_id_and_owner(self, bot_id, owner_id):
        return self._bot

    def get_by_id(self, bot_id):
        return self._bot


class _StubFileService:
    """Stands in for ``ResourceFileService`` — the engine seam the handlers use.

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

    ``entries`` may be a flat list (returned for any directory) or a dict keyed
    by the listed directory, which is what ``stat`` needs — it lists the
    *parent* and picks the entry out.
    """

    def __init__(self, entries: List[dict] | dict[str, List[dict]]):
        self._entries = entries
        self.listed: List[str] = []

    async def list_dir(self, *, path, **_kw) -> List[dict]:
        self.listed.append(path)
        if isinstance(self._entries, dict):
            return self._entries.get(path, [])
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

    async def list_dir(self, *, path, **_kw) -> List[dict]:
        """Whatever has been written, as the listing of its directory."""
        out = []
        for stored, data in self._files.items():
            parent = stored.rsplit("/", 1)[0] if "/" in stored else ""
            if parent == path:
                out.append(
                    _listed(
                        stored.rsplit("/", 1)[-1], rel=stored, size=len(data)
                    )
                )
        return out


# ── the contract: no record ids anywhere on this surface ─────────────


def test_the_file_entry_schema_carries_no_record_id():
    """``resource_id`` is gone from the contract rather than reported as ``""``.

    An empty string standing in for "no id" is a sentinel pretending to be an
    address: a generated client sees a non-optional string and cannot express
    absence except by comparing against a magic value. The same rule the router
    already applied to the timestamps.
    """
    assert set(FileEntry.model_fields) == {"path", "name", "type", "size"}


def test_the_resource_type_enum_is_files_and_folders_only():
    assert {t.value for t in OpenapiType} == {"file", "folder"}


# ── list_resources ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_returns_workspace_entries():
    """Entries come from the workspace. A record-backed listing could not see a
    file the bot produced itself — it has no record and never will."""
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
    # A folder has no size to report, whatever the listing carried.
    assert by_name["docs"].size is None


@pytest.mark.asyncio
async def test_legacy_list_preview_action_reads_the_file_instead_of_listing_it():
    """``action=preview`` is a file operation, not a list on a file path."""

    class _PreviewOnlyFileService(_StubReadFileService):
        async def list_dir(self, **_kw) -> List[dict]:
            raise AssertionError("preview must not call list_dir")

    file_svc = _PreviewOnlyFileService({"test.txt": b"hello"})
    response = await list_resources(
        page=PageParams(),
        owner_id="u1",
        bot_id="bot-x",
        path="test.txt",
        type=None,
        bot_repo=_StubBotRepo(),
        file_svc=file_svc,
        request=_request_without_trace(b"action=preview"),
    )

    assert isinstance(response, Response)
    body = json.loads(response.body)
    assert body["data"]["path"] == "test.txt"
    assert body["data"]["content"] == "hello"
    assert file_svc.read_paths == ["test.txt"]


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
        bot_repo=_StubBotRepo(),
        file_svc=file_svc,
        request=_request_without_trace(),
    )

    assert env.data.items[0].path == "a/b/c.txt"
    assert file_svc.listed == ["a/b"]


@pytest.mark.asyncio
async def test_list_never_exposes_the_container_path():
    """The entry's ``absolute_path`` is the engine-view container path and must
    not cross a public API."""
    file_svc = _StubListFileService([
        _listed("c.txt", rel="c.txt"),
    ])

    env = await list_resources(
        page=PageParams(), owner_id="u1", bot_id="bot-x", path="", type=None,
        bot_repo=_StubBotRepo(), file_svc=file_svc,
        request=_request_without_trace(),
    )

    assert "/home/admin" not in (env.data.items[0].path or "")


@pytest.mark.asyncio
async def test_list_folder_filter_returns_directories():
    file_svc = _StubListFileService([
        _listed("docs", rel="docs", is_dir=True),
        _listed("a.txt", rel="a.txt"),
    ])

    env = await list_resources(
        page=PageParams(), owner_id="u1", bot_id="bot-x", path="",
        type=OpenapiType.FOLDER,
        bot_repo=_StubBotRepo(), file_svc=file_svc,
        request=_request_without_trace(),
    )

    assert [i.name for i in env.data.items] == ["docs"]


@pytest.mark.asyncio
async def test_list_file_filter_excludes_directories():
    file_svc = _StubListFileService([
        _listed("docs", rel="docs", is_dir=True),
        _listed("a.txt", rel="a.txt"),
    ])

    env = await list_resources(
        page=PageParams(), owner_id="u1", bot_id="bot-x", path="",
        type=OpenapiType.FILE,
        bot_repo=_StubBotRepo(), file_svc=file_svc,
        request=_request_without_trace(),
    )

    assert [i.name for i in env.data.items] == ["a.txt"]


@pytest.mark.asyncio
async def test_list_paginates_the_directory_in_memory():
    """The engine's ``ListDirRequest`` carries no page, limit or cursor, so the
    whole directory is fetched and sliced here. ``total`` is therefore exact —
    and a later page costs exactly what the first one costs."""
    file_svc = _StubListFileService([
        _listed(f"f{n}.txt", rel=f"f{n}.txt") for n in range(5)
    ])

    env = await list_resources(
        page=PageParams(page=2, page_size=2),
        owner_id="u1", bot_id="bot-x", path="", type=None,
        bot_repo=_StubBotRepo(), file_svc=file_svc,
        request=_request_without_trace(),
    )

    assert env.data.total == 5
    assert [i.name for i in env.data.items] == ["f2.txt", "f3.txt"]
    # One directory fetch, whichever page was asked for.
    assert file_svc.listed == [""]


@pytest.mark.asyncio
async def test_list_reads_x_trace_id_from_request():
    env = await list_resources(
        page=PageParams(), owner_id="u1", bot_id="bot-x", path="", type=None,
        bot_repo=_StubBotRepo(), file_svc=_StubListFileService([]),
        request=_request_with_trace("trace-list-1"),
    )
    assert env.request_id == "trace-list-1"


@pytest.mark.asyncio
async def test_list_empty_workspace_returns_empty_page():
    env = await list_resources(
        page=PageParams(), owner_id="u1", bot_id="bot-x", path="", type=None,
        bot_repo=_StubBotRepo(), file_svc=_StubListFileService([]),
        request=_request_without_trace(),
    )
    assert env.data.total == 0
    assert env.data.items == []


@pytest.mark.asyncio
async def test_list_of_an_absent_directory_is_an_empty_page_not_a_500():
    """The baas providers re-raise the upstream 404 instead of answering
    "nothing there", while providers returning ``None`` already produce an empty
    page — so without normalizing, the same request is a 500 on one and an empty
    page on the others."""

    class _MissingDirService:
        async def list_dir(self, **_kw):
            raise _http_status(404)

    env = await list_resources(
        page=PageParams(page=1, page_size=10),
        owner_id="u1",
        bot_id="bot-x",
        path="nope",
        bot_repo=_StubBotRepo(),
        file_svc=_MissingDirService(),
        request=_request_without_trace(),
    )

    assert env.data.total == 0
    assert env.data.items == []


@pytest.mark.asyncio
async def test_list_does_not_swallow_other_upstream_statuses():
    """Only 404 is an ordinary answer; an upstream fault must still surface."""

    class _FailingDirService:
        async def list_dir(self, **_kw):
            raise _http_status(503)

    with pytest.raises(httpx.HTTPStatusError):
        await list_resources(
            page=PageParams(page=1, page_size=10),
            owner_id="u1",
            bot_id="bot-x",
            path="docs",
            bot_repo=_StubBotRepo(),
            file_svc=_FailingDirService(),
            request=_request_without_trace(),
        )


@pytest.mark.asyncio
async def test_list_rejects_a_directory_escaping_the_workspace():
    file_svc = _StubListFileService([])

    resp = await list_resources(
        page=PageParams(), owner_id="u1", bot_id="bot-x", path="../../etc",
        type=None, bot_repo=_StubBotRepo(),
        file_svc=file_svc, request=_request_without_trace(),
    )

    assert resp.status_code == 400
    assert file_svc.listed == []


# ── stat: one entry, addressed by path ───────────────────────────────
#
# Replaces both ``GET /{resource_id}`` (which could not address a file at all)
# and ``check-name`` (which asked the same existence question two ways). It is
# resolved by listing the parent, so stat and list read the same seam and cannot
# report different types or sizes for one file.


@pytest.mark.asyncio
async def test_stat_returns_the_entry_for_a_path():
    file_svc = _StubListFileService({
        "docs": [
            _listed("a.txt", rel="docs/a.txt", size=42),
            _listed("b.txt", rel="docs/b.txt", size=7),
        ]
    })

    env = await stat_resource(
        path="docs/a.txt",
        owner_id="u1",
        bot_id="bot-x",
        bot_repo=_StubBotRepo(),
        file_svc=file_svc,
        request=_request_without_trace(),
    )

    assert env.code == CODE_OK
    assert env.data.path == "docs/a.txt"
    assert env.data.name == "a.txt"
    assert env.data.type == OpenapiType.FILE
    assert env.data.size == 42
    # The *parent* is listed, not the file itself.
    assert file_svc.listed == ["docs"]


@pytest.mark.asyncio
async def test_stat_of_a_root_level_entry_lists_the_workspace_root():
    file_svc = _StubListFileService({"": [_listed("notes.md", rel="notes.md")]})

    env = await stat_resource(
        path="notes.md",
        owner_id="u1",
        bot_id="bot-x",
        bot_repo=_StubBotRepo(),
        file_svc=file_svc,
        request=_request_without_trace(),
    )

    assert env.data.path == "notes.md"
    assert file_svc.listed == [""]


@pytest.mark.asyncio
async def test_stat_reports_a_directory_as_a_folder():
    file_svc = _StubListFileService({"": [_listed("docs", rel="docs", is_dir=True)]})

    env = await stat_resource(
        path="docs",
        owner_id="u1",
        bot_id="bot-x",
        bot_repo=_StubBotRepo(),
        file_svc=file_svc,
        request=_request_without_trace(),
    )

    assert env.data.type == OpenapiType.FOLDER
    assert env.data.size is None


@pytest.mark.asyncio
async def test_stat_404s_a_path_that_is_not_there():
    file_svc = _StubListFileService({"docs": [_listed("a.txt", rel="docs/a.txt")]})

    with pytest.raises(HTTPException) as exc:
        await stat_resource(
            path="docs/gone.txt",
            owner_id="u1",
            bot_id="bot-x",
            bot_repo=_StubBotRepo(),
            file_svc=file_svc,
            request=_request_without_trace(),
        )

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_stat_404s_when_the_parent_directory_is_absent():
    """An absent parent lists empty rather than raising, so the entry is simply
    not found — the same answer as an absent leaf."""

    class _MissingDirService:
        async def list_dir(self, **_kw):
            raise _http_status(404)

    with pytest.raises(HTTPException) as exc:
        await stat_resource(
            path="nope/a.txt",
            owner_id="u1",
            bot_id="bot-x",
            bot_repo=_StubBotRepo(),
            file_svc=_MissingDirService(),
            request=_request_without_trace(),
        )

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_stat_requires_a_path():
    """The workspace root has no entry of its own — there is nothing to report
    about it — so the empty path the listing accepts is refused here."""
    resp = await stat_resource(
        path="",
        owner_id="u1",
        bot_id="bot-x",
        bot_repo=_StubBotRepo(),
        file_svc=_StubListFileService([]),
        request=_request_without_trace(),
    )

    assert resp.status_code == 400
    assert json.loads(resp.body)["message"] == "Invalid resource path"


@pytest.mark.asyncio
async def test_stat_rejects_a_path_escaping_the_workspace():
    file_svc = _StubListFileService([])

    resp = await stat_resource(
        path="../../etc/passwd",
        owner_id="u1",
        bot_id="bot-x",
        bot_repo=_StubBotRepo(),
        file_svc=file_svc,
        request=_request_without_trace(),
    )

    assert resp.status_code == 400
    assert file_svc.listed == []


@pytest.mark.asyncio
async def test_stat_and_list_agree_because_they_read_one_seam():
    """The pair that used to diverge: a record said one thing and the workspace
    another. Both now come from ``list_dir``, so they cannot."""
    entries = {"docs": [_listed("a.txt", rel="docs/a.txt", size=99)]}

    listed = await list_resources(
        page=PageParams(), owner_id="u1", bot_id="bot-x", path="docs", type=None,
        bot_repo=_StubBotRepo(), file_svc=_StubListFileService(entries),
        request=_request_without_trace(),
    )
    statted = await stat_resource(
        path="docs/a.txt", owner_id="u1", bot_id="bot-x",
        bot_repo=_StubBotRepo(), file_svc=_StubListFileService(entries),
        request=_request_without_trace(),
    )

    assert listed.data.items[0] == statted.data


# ── the shared user_id dependency ────────────────────────────────────


@pytest.mark.asyncio
async def test_no_handler_can_fall_back_to_a_bot_derived_owner():
    """Owner-scoped routes take their owner from the request, or answer 401.

    Handlers used to be handed the principal and resolve the owner themselves,
    and this file checked each one refused a ``None`` principal — the property
    being that ``owner_id`` is never quietly recovered from ``bot_repo`` for a
    caller who supplied no identity. The resolution has since moved into
    ``require_user_id``, one dependency the whole surface shares, so per-handler
    copies would only re-test the same function.

    What still needs checking is that no route escapes it. A resources route
    added later without the dependency is exactly the silent fallback the
    original checks existed to prevent, so the guard is asserted over the mounted
    routes rather than per handler — and the fail-closed half is asserted once,
    on the seam itself.
    """
    mounted = [
        route
        for route in _api_routes(public_router())
        if route.path.startswith("/openapi/v1/bots/{bot_id}/resources")
    ]
    # 8 routes (download-dir included). The count is a tripwire — a resources
    # route added without the shared user_id dependency is exactly the silent
    # owner fallback this guard exists to prevent.
    assert len(mounted) == 8, [r.path for r in mounted]
    # The only path parameter is the bot the operation addresses. This used to
    # read "no path parameters at all", which stopped being sayable when the
    # group moved from /bots/resources?bot_id= to /bots/{bot_id}/resources — but
    # the property it was really protecting still holds: the group is addressed
    # by workspace path, never by a record id, so a future ``/{resource_id}``
    # still cannot creep in without failing here.
    assert {
        segment
        for route in mounted
        for segment in route.path.split("/")
        if segment.startswith("{")
    } == {"{bot_id}"}, [r.path for r in mounted]

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


# ── upload_resource ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_upload_hands_the_workspace_relative_path_to_the_engine_seam():
    file_svc = _StubFileService()

    env = await upload_resource(
        path="hello.txt",
        content=b"file bytes",
        owner_id="u1",
        bot_id="bot-x",
        factory=_StubFactory(),
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
        factory=_StubFactory(),
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
@pytest.mark.parametrize(
    "path",
    [
        ".private.md",
        "docs/.private.md",
        "AGENTS.md",
        # Protected *ancestors*: the leaf is an ordinary name, but creating
        # the path brings a hidden directory into existence along the way.
        ".private/file.md",
        "docs/.private/sub.md",
        "AGENTS.md/sub.txt",
    ],
)
async def test_upload_403s_a_read_only_path(path):
    """The surface must not be talked into making something it then refuses to
    manage: listings hide dotfiles and the root identity files, and delete
    refuses them, so accepting the upload would leave an entry this API can
    neither show nor remove."""
    file_svc = _StubFileService()

    with pytest.raises(HTTPException) as exc:
        await upload_resource(
            path=path,
            content=b"x",
            owner_id="u1",
            bot_id="bot-x",
            factory=_StubFactory(),
            bot_repo=_StubBotRepo(),
            file_svc=file_svc,
            request=_request_without_trace(),
        )

    assert exc.value.status_code == 403
    assert file_svc.upload_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("path", [".hidden", "docs/.hidden", ".hidden/deep"])
async def test_mkdir_403s_a_dot_prefixed_directory_at_any_depth(path):
    """Same reason as the upload guard — a hidden directory would be invisible
    to listing and refused by delete."""
    file_svc = _StubReadFileService({})

    with pytest.raises(HTTPException) as exc:
        await create_directory(
            path=path,
            owner_id="u1",
            bot_id="bot-x",
            bot_repo=_StubBotRepo(),
            file_svc=file_svc,
            request=_request_without_trace(),
        )

    assert exc.value.status_code == 403
    assert file_svc.made_dirs == []


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
async def test_upload_overwrite_replaces_an_occupied_path():
    """Without this the only way to change a file's content is delete-then-upload
    — two calls, racy, and the file is gone outright if the second one fails."""
    service = _StubService()
    file_svc = _StubReadFileService({"docs/a.txt": b"old"})

    env = await upload_resource(
        path="docs/a.txt",
        content=b"new bytes",
        overwrite=True,
        owner_id="u1",
        bot_id="bot-x",
        factory=_StubFactory(service),
        bot_repo=_StubBotRepo(),
        file_svc=file_svc,
        request=_request_without_trace(),
    )

    assert env.code == CODE_CREATED
    assert env.data.path == "docs/a.txt"
    assert file_svc._files["docs/a.txt"] == b"new bytes"


@pytest.mark.asyncio
async def test_upload_overwrite_replaces_the_record_rather_than_adding_one():
    """``record_uploaded_file`` always inserts, so leaving the prior row would
    give one path two rows — and publish the file twice in the manifest."""
    service = _StubService()

    await upload_resource(
        path="docs/a.txt",
        content=b"new",
        overwrite=True,
        owner_id="u1",
        bot_id="bot-x",
        factory=_StubFactory(service),
        bot_repo=_StubBotRepo(),
        file_svc=_StubReadFileService({"docs/a.txt": b"old"}),
        request=_request_without_trace(),
    )

    assert service.record_deletes == ["docs/a.txt"]
    assert [r["path"] for r in service.recorded] == ["docs/a.txt"]


@pytest.mark.asyncio
async def test_upload_without_overwrite_leaves_the_record_alone():
    """A fresh upload has no prior row to drop, and dropping on this branch
    would delete rows for a path the caller was refused anyway."""
    service = _StubService()

    await upload_resource(
        path="docs/new.txt",
        content=b"x",
        owner_id="u1",
        bot_id="bot-x",
        factory=_StubFactory(service),
        bot_repo=_StubBotRepo(),
        file_svc=_StubReadFileService({}),
        request=_request_without_trace(),
    )

    assert service.record_deletes == []
    assert [r["path"] for r in service.recorded] == ["docs/new.txt"]


@pytest.mark.asyncio
async def test_upload_overwrite_does_not_roll_the_file_back_on_a_record_failure():
    """A fresh upload rolls back because its retry is blocked by the 409. An
    overwrite's prior bytes are already gone, so deleting the file would destroy
    content rather than restore it — and the retry is not blocked, because an
    overwrite does not 409. So the bytes stay and the caller is told to retry."""

    class _ExplodingFactory:
        def create(self, *, bot_id):
            raise RuntimeError("repo down")

    file_svc = _StubReadFileService({"a.txt": b"old"})

    with pytest.raises(HTTPException) as excinfo:
        await upload_resource(
            path="a.txt",
            content=b"new",
            overwrite=True,
            owner_id="u1",
            bot_id="bot-x",
            factory=_ExplodingFactory(),
            bot_repo=_StubBotRepo(),
            file_svc=file_svc,
            request=_request_without_trace(),
        )

    assert excinfo.value.status_code == 502
    # The new bytes are still there — not deleted out from under the caller.
    assert file_svc.deleted_paths == []
    assert file_svc._files["a.txt"] == b"new"


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
        factory=_StubFactory(),
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
        factory=_StubFactory(),
        bot_repo=_StubBotRepo(),
        file_svc=_StubFileService(),
        request=_request_with_trace("trace-up-1"),
    )

    assert env.request_id == "trace-up-1"


# ── file read: download / preview, addressed by workspace path ───────


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
    # The preview reports the path it was asked for, so the response names the
    # address the caller can reuse — there is no id to name it by.
    assert env.data.path == "a.txt"


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
@pytest.mark.parametrize("handler", [download_file, preview_file])
async def test_read_404s_when_the_provider_reports_the_file_missing(handler):
    """The baas providers re-raise the upstream status rather than returning
    ``None`` for a missing file, and that loudness is load-bearing at the device
    — a swallowed 401 once read as "file gone" and silently dropped promoted
    files. It stops being a failure only here, where 404 is a documented
    answer."""
    with pytest.raises(HTTPException) as exc:
        await handler(
            path="gone.txt",
            owner_id="u1",
            bot_id="bot-x",
            bot_repo=_StubBotRepo(),
            file_svc=_StubReadFileService({}, read_raises=_http_status(404)),
            request=_request_without_trace(),
        )

    assert exc.value.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("code", [401, 500, 503])
async def test_read_does_not_swallow_other_upstream_statuses(code):
    """Only 404 is an ordinary answer. Anything else is an upstream fault and
    must stay unmapped, which is what a 500 reports."""
    with pytest.raises(httpx.HTTPStatusError):
        await download_file(
            path="a.txt",
            owner_id="u1",
            bot_id="bot-x",
            bot_repo=_StubBotRepo(),
            file_svc=_StubReadFileService({}, read_raises=_http_status(code)),
            request=_request_without_trace(),
        )


@pytest.mark.asyncio
async def test_download_serves_an_empty_file_rather_than_404ing_it():
    """An empty file exists and appears in a listing, so 404ing it would be the
    workspace and the API disagreeing about what exists — the divergence this
    change removes. Only ``None`` means absent."""
    resp = await download_file(
        path="empty.txt",
        owner_id="u1",
        bot_id="bot-x",
        bot_repo=_StubBotRepo(),
        file_svc=_StubReadFileService({"empty.txt": b""}),
        request=_request_without_trace(),
    )

    assert isinstance(resp, Response)
    assert resp.body == b""


@pytest.mark.asyncio
async def test_preview_of_an_empty_file_is_empty_not_missing():
    env = await preview_file(
        path="empty.txt",
        owner_id="u1",
        bot_id="bot-x",
        bot_repo=_StubBotRepo(),
        file_svc=_StubReadFileService({"empty.txt": b""}),
        request=_request_without_trace(),
    )

    assert env.data.content == ""


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

    env = await delete_file(
        path="docs/a.txt",
        owner_id="u1",
        bot_id="bot-x",
        factory=_StubFactory(),
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

    with pytest.raises(HTTPException) as exc:
        await delete_file(
            path="docs/a.txt",
            owner_id="u1",
            bot_id="bot-x",
            factory=_StubFactory(),
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
    assert env.data.name == "spec"
    assert file_svc.made_dirs == ["docs/spec"]


# ── End-to-end integration ──────────────────────────────────────────
#
# The stub-service tests above pass even when the real factory's service is
# missing methods (the stub supplies them). This block uses the REAL
# ``ResourceServiceFactory`` (factory.create → real slim ``ResourceService``)
# backed by a REAL in-memory repository, so the record writes the publish
# pipeline depends on actually run. A missing method or a handler↔service
# signature mismatch fails here instead of in production.


class _InMemoryResourceRepo:
    """Minimal real ``ResourceRepositoryProtocol`` for integration tests.

    Non-mock: create stores a row, get_by_id reads it back, delete
    soft-deletes it. The slim service's real logic runs against this.
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

    ``ResourceServiceFactory.__init__`` is ``@inject``-decorated, but direct
    construction with an explicit ``repository=`` bypasses injection (same path
    the injector takes in prod). ``factory.create(bot_id=…)`` returns the real
    slim ``ResourceService`` — the exact object the openapi_v1 handlers receive.
    """
    repo = _InMemoryResourceRepo()
    factory = ResourceServiceFactory(repository=repo)
    return factory, repo


@pytest.mark.asyncio
async def test_real_factory_service_supports_every_handler_path_e2e():
    """upload → stat → list → download → preview → delete → re-upload, through
    the REAL factory and slim service against a real in-memory repository."""
    factory, repo = _real_factory_with_inmemory_repo()
    file_svc = _StubReadFileService({})

    # 1. upload a file by workspace path; the real factory writes the record the
    #    publish pipeline reads.
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
    file_row = list(repo._rows.values())[-1]
    assert file_row["attributes"]["path"] == "docs/hello.txt"
    assert file_row["user_id"] == "u1"

    # 2. stat it back by the path the upload reported — the address a client
    #    actually round-trips.
    env_s = await stat_resource(
        path="docs/hello.txt",
        owner_id="u1",
        bot_id="bot-x",
        bot_repo=_StubBotRepo(),
        file_svc=file_svc,
        request=_request_without_trace(),
    )
    assert env_s.data.path == "docs/hello.txt"
    assert env_s.data.size == len(b"file bytes")

    # 3. list its directory.
    env_l = await list_resources(
        page=PageParams(),
        owner_id="u1",
        bot_id="bot-x",
        path="docs",
        type=None,
        bot_repo=_StubBotRepo(),
        file_svc=file_svc,
        request=_request_without_trace(),
    )
    assert [i.path for i in env_l.data.items] == ["docs/hello.txt"]

    # 4. download and preview it.
    response = await download_file(
        path="docs/hello.txt",
        owner_id="u1",
        bot_id="bot-x",
        bot_repo=_StubBotRepo(),
        file_svc=file_svc,
        request=_request_without_trace(),
    )
    assert response.body == b"file bytes"

    env_p = await preview_file(
        path="docs/hello.txt",
        owner_id="u1",
        bot_id="bot-x",
        bot_repo=_StubBotRepo(),
        file_svc=file_svc,
        request=_request_without_trace(),
    )
    assert env_p.data.content == "file bytes"

    # 5. delete it — the file goes, and the real record goes with it.
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

    # 6. the path is free again, so the same upload succeeds rather than 409ing.
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


@pytest.mark.asyncio
async def test_sequential_overwrite_leaves_one_live_record_for_the_path_e2e():
    """Against the real service: the replaced row is soft-deleted and one live
    row remains, so the publish manifest lists the file once.

    **Sequential**, and the name says so deliberately. The drop and the insert
    are two statements with no lock between them, so concurrent overwrites on one
    path can still leave two live rows — see the router comment. Naming this
    ``leaves_one_live_record`` unqualified would read as a concurrency invariant
    the code does not provide.
    """
    factory, repo = _real_factory_with_inmemory_repo()
    file_svc = _StubReadFileService({})

    await upload_resource(
        path="docs/a.txt", content=b"one", owner_id="u1", bot_id="bot-x",
        factory=factory, bot_repo=_StubBotRepo(), file_svc=file_svc,
        request=_request_without_trace(),
    )
    await upload_resource(
        path="docs/a.txt", content=b"two", overwrite=True, owner_id="u1",
        bot_id="bot-x", factory=factory, bot_repo=_StubBotRepo(),
        file_svc=file_svc, request=_request_without_trace(),
    )

    live = [
        r for r in repo._rows.values()
        if r.get("status") != "deleted"
        and (r.get("attributes") or {}).get("path") == "docs/a.txt"
    ]
    assert len(live) == 1
    assert file_svc._files["docs/a.txt"] == b"two"


@pytest.mark.asyncio
async def test_file_reads_are_scoped_to_the_requested_bot():
    """Replaces the cross-bot ``resource_id`` isolation tests.

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
async def test_upload_returns_502_when_the_device_write_fails_e2e():
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
    assert env.data.path == "docs/a.txt"
    row = list(repo._rows.values())[-1]
    assert row["user_id"] == "u1"
    assert row["created_by"] == "u1"
    assert row["name"] == "a.txt"  # leaf
    assert row["attributes"]["path"] == "docs/a.txt"  # full workspace-relative
    assert row["attributes"]["parent_path"] == "docs"  # dirname, for the console
    assert row["source"] == "upload"


@pytest.mark.asyncio
async def test_upload_reports_the_file_the_way_a_listing_would():
    """The row is the publish pipeline's input, not something this API reads
    back. Sourcing the response from it would make the upload the one file
    response shaped differently from every other."""
    factory, _ = _real_factory_with_inmemory_repo()

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

    # What the caller can actually use: the path, which every file endpoint takes.
    assert env.data.path == "docs/a.txt"
    assert env.data.name == "a.txt"
    assert env.data.size == len(b"file bytes")


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
async def test_upload_treats_a_refused_rollback_as_a_failed_one(caplog):
    """A provider refuses by returning ``False``, not by raising, so catching
    only exceptions would miss half of it. Both arms land in the same state —
    file on disk, no record — and both must say so, because the next upload of
    this path 409s against a file the operator has no record of."""

    class _ExplodingFactory:
        def create(self, *, bot_id):
            raise RuntimeError("repo down")

    class _RefusingFileService(_StubFileService):
        async def delete(self, *, path, **_kw) -> bool:
            self.deleted_paths.append(path)
            return False

    file_svc = _RefusingFileService()

    with caplog.at_level(logging.ERROR):
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
    assert "on disk with no record" in caplog.text


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
async def test_upload_409_takes_precedence_over_the_502_path():
    """With both conditions live (occupied path AND a failing device), the
    occupancy check runs first, so the 409 surfaces — not the 502. Pins the
    ordering: an upload must never attempt a write it would refuse anyway.

    Occupancy is decided by the workspace rather than by a row, so the seed is a
    file present on the device, not a record.
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


# ── directory download: a zip of the subtree, addressed by workspace path ────


class _StubTreeFileService(_StubReadFileService):
    """Adds the directory walk the download-dir handler consumes.

    ``files`` keys are workspace-relative paths; the walk yields the entries
    under the requested prefix with names relative to it — the same contract
    ``ResourceFileService.iter_directory_files`` documents. ``tree_raises``
    fails the walk; ``missing`` prefixes answer ``ResourceNotFoundError``.
    """

    def __init__(
        self,
        files: dict[str, bytes] | None = None,
        *,
        tree_raises: Exception | None = None,
        missing: set[str] | None = None,
    ):
        super().__init__(files)
        self.tree_raises = tree_raises
        self.missing = set(missing or ())
        self.tree_paths: List[str] = []

    async def iter_directory_files(self, *, path, **_kw):
        self.tree_paths.append(path)
        if self.tree_raises is not None:
            raise self.tree_raises
        if path in self.missing:
            raise ResourceNotFoundError(f"no such directory: {path!r}")
        prefix = f"{path}/" if path else ""
        for name in sorted(self._files):
            if path and not name.startswith(prefix):
                continue
            yield name[len(prefix):], self._files[name]


def _zip_of(response: FileResponse) -> zipfile.ZipFile:
    with open(response.path, "rb") as fh:
        return zipfile.ZipFile(io.BytesIO(fh.read()))


async def _cleanup(response: FileResponse) -> None:
    """Run the background cleanup the server would, and prove it worked."""
    path = response.path
    assert os.path.exists(path)
    await response.background()
    assert not os.path.exists(path)


@pytest.mark.asyncio
async def test_download_dir_returns_a_zip_of_the_subtree():
    file_svc = _StubTreeFileService(
        {
            "docs/a.txt": b"aa",
            "docs/deep/b.txt": b"bb",
            "other.txt": b"o",
        }
    )

    response = await download_directory(
        path="docs",
        owner_id="u1",
        bot_id="bot-x",
        bot_repo=_StubBotRepo(),
        file_svc=file_svc,
        request=_request_without_trace(),
    )

    assert isinstance(response, FileResponse)
    assert response.media_type == "application/zip"
    assert response.headers["content-disposition"] == (
        "attachment; filename*=UTF-8''docs.zip"
    )
    with _zip_of(response) as zf:
        # Flat under the requested folder; "other.txt" is outside it.
        assert zf.namelist() == ["docs/", "docs/a.txt", "docs/deep/b.txt"]
        assert zf.read("docs/a.txt") == b"aa"
        assert zf.read("docs/deep/b.txt") == b"bb"
    assert file_svc.tree_paths == ["docs"]
    await _cleanup(response)


@pytest.mark.asyncio
async def test_download_dir_of_the_root_zips_the_whole_workspace():
    file_svc = _StubTreeFileService({"docs/a.txt": b"aa", "top.txt": b"t"})

    response = await download_directory(
        path="",
        owner_id="u1",
        bot_id="bot-x",
        bot_repo=_StubBotRepo(),
        file_svc=file_svc,
        request=_request_without_trace(),
    )

    assert response.headers["content-disposition"] == (
        "attachment; filename*=UTF-8''workspace.zip"
    )
    with _zip_of(response) as zf:
        assert zf.namelist() == [
            "workspace/",
            "workspace/docs/a.txt",
            "workspace/top.txt",
        ]
    await _cleanup(response)


@pytest.mark.asyncio
async def test_download_dir_rejects_a_path_escaping_the_workspace():
    file_svc = _StubTreeFileService({})

    resp = await download_directory(
        path="../secrets",
        owner_id="u1",
        bot_id="bot-x",
        bot_repo=_StubBotRepo(),
        file_svc=file_svc,
        request=_request_without_trace(),
    )

    assert resp.status_code == 400
    assert file_svc.tree_paths == []  # nothing was walked


@pytest.mark.asyncio
async def test_download_dir_404_when_the_directory_is_absent():
    resp = await download_directory(
        path="nope",
        owner_id="u1",
        bot_id="bot-x",
        bot_repo=_StubBotRepo(),
        file_svc=_StubTreeFileService({}, missing={"nope"}),
        request=_request_without_trace(),
    )

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_download_dir_404s_when_the_provider_raises_the_upstream_404():
    """The baas shape of "directory absent" — an upstream 404, not a ``None``.

    ``baas_device_filesystem.list_dir`` re-raises the upstream status rather
    than answering ``None``, so on the live baas stack the walk surfaces the
    missing directory as an ``httpx.HTTPStatusError``. A missing directory is
    this route's documented 404, same as the ``None``-provider shape the test
    above covers — not a 500.
    """
    with pytest.raises(HTTPException) as exc:
        await download_directory(
            path="nope",
            owner_id="u1",
            bot_id="bot-x",
            bot_repo=_StubBotRepo(),
            file_svc=_StubTreeFileService({}, tree_raises=_http_status(404)),
            request=_request_without_trace(),
        )

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_download_dir_surfaces_an_upstream_fault():
    """Only 404 is an ordinary answer; an upstream fault must still surface."""

    with pytest.raises(httpx.HTTPStatusError):
        await download_directory(
            path="docs",
            owner_id="u1",
            bot_id="bot-x",
            bot_repo=_StubBotRepo(),
            file_svc=_StubTreeFileService({}, tree_raises=_http_status(503)),
            request=_request_without_trace(),
        )


@pytest.mark.asyncio
async def test_download_dir_empty_directory_is_a_valid_root_only_archive():
    """Empty is not missing: an existing empty directory downloads as an
    archive holding just its root entry — the walk can tell the two apart."""
    response = await download_directory(
        path="empty",
        owner_id="u1",
        bot_id="bot-x",
        bot_repo=_StubBotRepo(),
        file_svc=_StubTreeFileService({"docs/a.txt": b"aa"}),
        request=_request_without_trace(),
    )

    with _zip_of(response) as zf:
        assert zf.namelist() == ["empty/"]
    await _cleanup(response)


@pytest.mark.asyncio
async def test_download_dir_413_when_a_cap_is_exceeded():
    resp = await download_directory(
        path="docs",
        owner_id="u1",
        bot_id="bot-x",
        bot_repo=_StubBotRepo(),
        file_svc=_StubTreeFileService(
            {"docs/a.txt": b"aa"},
            tree_raises=DirectoryTooLargeError("over the cap"),
        ),
        request=_request_without_trace(),
    )

    assert resp.status_code == 413
    assert json.loads(resp.body)["message"] == "Directory too large to download"


@pytest.mark.asyncio
async def test_download_dir_propagates_a_mid_walk_failure():
    """An unmapped failure re-raises (envelope_errors hands it to the app
    500 handler) — and the partial archive is deleted by ``build_directory_zip``
    (pinned in test_zip_build.py), so no half zip is ever served."""
    with pytest.raises(RuntimeError):
        await download_directory(
            path="docs",
            owner_id="u1",
            bot_id="bot-x",
            bot_repo=_StubBotRepo(),
            file_svc=_StubTreeFileService(
                {"docs/a.txt": b"aa"}, tree_raises=RuntimeError("walk blew up")
            ),
            request=_request_without_trace(),
        )


@pytest.mark.asyncio
async def test_download_dir_utf8_folder_name_in_the_disposition():
    response = await download_directory(
        path="文档",
        owner_id="u1",
        bot_id="bot-x",
        bot_repo=_StubBotRepo(),
        file_svc=_StubTreeFileService({"文档/a.txt": b"aa"}),
        request=_request_without_trace(),
    )

    assert response.headers["content-disposition"] == (
        "attachment; filename*=UTF-8''%E6%96%87%E6%A1%A3.zip"
    )
    with _zip_of(response) as zf:
        assert zf.namelist() == ["文档/", "文档/a.txt"]
    await _cleanup(response)
