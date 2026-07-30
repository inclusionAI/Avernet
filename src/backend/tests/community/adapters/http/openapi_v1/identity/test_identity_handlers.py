"""openapi_v1 identity handler unit tests.

Direct handler invocation (退路 B per task spec): bypasses FastAPI's
dependency wiring and supplies a stub service. ``principal`` carries
``{"user_id": "u1"}`` so ``caller_owner_id`` resolves the caller. Handlers
take a required ``request: Request`` (mirroring the bots router), so tests
pass a ``SimpleNamespace`` stub: ``state.trace_id`` unset ⇒ empty
``request_id`` (the tracer middleware did not run), or set to a known value
to assert it is threaded into the envelope via ``responses.envelope``.
"""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from agentclaw.community.adapters.http.openapi_v1.contracts import (
    CODE_OK,
    Envelope,
)
from agentclaw.community.adapters.http.openapi_v1.identity.router import (
    get_bot_identity_file,
    list_bot_identity_files,
    update_bot_identity_file,
)
from agentclaw.community.adapters.http.openapi_v1.identity.schemas import (
    IdentityFile,
    IdentityFileList,
    IdentityFileRef,
    IdentityFileType,
    IdentityFileWrite,
)


# The 16 whitelisted identity file types (physical names, with ``.md``).
_ALL_IDENTITY_FILES = [
    "RULES.md",
    "OKR.md",
    "SAFETY.md",
    "SOUL.md",
    "OUTPUT.md",
    "MEMORY.md",
    "IDENTITY.md",
    "AGENTS.md",
    "USER.md",
    "TOOLS.md",
    "HEARTBEAT.md",
    "BOOTSTRAP.md",
    "KNOWLEDGE.md",
    "CLAUDE.md",
    "GREETING.md",
    "README.md",
]


def _request_without_trace() -> SimpleNamespace:
    """A request whose tracer middleware did not run — ``state.trace_id`` unset.

    ``responses._trace_id`` reads ``request.state.trace_id`` and falls back to
    ``""`` when absent, so the envelope's ``request_id`` is empty (mirrors the
    prod path before the tracer middleware stamps the id).
    """
    return SimpleNamespace(state=SimpleNamespace())


def _request_with_trace(trace_id: str) -> SimpleNamespace:
    """A request whose tracer middleware stamped ``trace_id`` on ``state``."""
    return SimpleNamespace(state=SimpleNamespace(trace_id=trace_id))


class _StubIdentityService:
    """Minimal stub satisfying the IdentityService.list_bot_files seam."""

    def __init__(self, presence):
        # presence: list[(file_type_str_with_dot_md, exists_bool)]
        self._presence = presence
        self.last_call_kwargs: dict = {}

    async def list_bot_files(
        self,
        entity_type,
        entity_id,
        bot_id,
        owner_id,
        *,
        engine_type=None,
    ):
        self.last_call_kwargs = {
            "entity_type": entity_type,
            "entity_id": entity_id,
            "bot_id": bot_id,
            "owner_id": owner_id,
            "engine_type": engine_type,
        }
        return self._presence


def _all_present() -> list[tuple[str, bool]]:
    return [(ft, True) for ft in _ALL_IDENTITY_FILES]


# ── list_bot_identity_files handler wiring (Identity Task 1) ────────────


@pytest.mark.asyncio
async def test_list_bot_identity_files_returns_all_16_with_exists():
    service = _StubIdentityService(_all_present())

    env = await list_bot_identity_files(
        bot_id="bot-x",
        principal={"user_id": "u1"},
        identity_service=service,
        request=_request_without_trace(),
    )

    assert isinstance(env, Envelope)
    assert env.code == CODE_OK
    assert env.message == "OK"
    assert env.request_id == ""
    assert env.data is not None
    assert isinstance(env.data, IdentityFileList)
    assert env.data.bot_id == "bot-x"
    assert len(env.data.files) == 16
    first = env.data.files[0]
    assert first.type == IdentityFileType.RULES
    assert first.exists is True
    assert first.file_path == "identity/RULES.md"
    # owner → entity params threaded through (personal bot owner is a staff entity)
    assert service.last_call_kwargs["entity_type"] == "staff"
    assert service.last_call_kwargs["entity_id"] == "u1"
    assert service.last_call_kwargs["owner_id"] == "u1"
    assert service.last_call_kwargs["bot_id"] == "bot-x"


@pytest.mark.asyncio
async def test_list_bot_identity_files_marks_absent_files_false():
    # Half present, half absent — exists flag must reflect each probe.
    presence = [(ft, i % 2 == 0) for i, ft in enumerate(_ALL_IDENTITY_FILES)]
    service = _StubIdentityService(presence)

    env = await list_bot_identity_files(
        bot_id="bot-x",
        principal={"user_id": "u1"},
        identity_service=service,
        request=_request_without_trace(),
    )

    assert env.code == CODE_OK
    assert len(env.data.files) == 16
    assert env.data.files[0].exists is True
    assert env.data.files[1].exists is False


@pytest.mark.asyncio
async def test_list_bot_identity_files_reads_trace_id_from_request_state():
    service = _StubIdentityService(_all_present())
    request = _request_with_trace("trace-identity-1")

    env = await list_bot_identity_files(
        bot_id="bot-x",
        principal={"user_id": "u1"},
        identity_service=service,
        request=request,
    )

    assert env.request_id == "trace-identity-1"


@pytest.mark.asyncio
async def test_list_bot_identity_files_400_when_entity_type_invalid():
    # Service raises ValueError (invalid entity_type) → handler maps to 400.
    from fastapi import HTTPException

    class _RaisingService:
        async def list_bot_files(
            self, entity_type, entity_id, bot_id, owner_id, *, engine_type=None
        ):
            raise ValueError(f"Invalid entity_type: {entity_type}")

    service = _RaisingService()

    with pytest.raises(HTTPException) as exc:
        await list_bot_identity_files(
            bot_id="bot-x",
            principal={"user_id": "u1"},
            identity_service=service,
            request=_request_without_trace(),
        )
    assert exc.value.status_code == 400


# ── get_bot_identity_file / update_bot_identity_file (Identity Task 2) ──


class _BotFileResponse(SimpleNamespace):
    """Mimic BotIdentityFileResponse (legacy pydantic model) for stubbing."""


class _BotFileUpdateResponse(SimpleNamespace):
    """Mimic BotIdentityFileUpdateResponse (legacy pydantic model) for stubbing."""


class _StubGetUpdateService:
    """Stub IdentityService for get_bot_file / update_bot_file seams.

    Records the last call so tests can assert the ``<type>.md`` suffix is
    threaded through (validate_file_type requires the physical form).
    """

    def __init__(
        self,
        *,
        get_resp=None,
        update_resp=None,
        raise_on_get=None,
        raise_on_update=None,
    ):
        self._get_resp = get_resp or _BotFileResponse(
            success=True,
            file_type="RULES.md",
            entity_type="staff",
            entity_id="u1",
            bot_id="bot-x",
            content="# rules",
            file_path="identity/RULES.md",
        )
        self._update_resp = update_resp or _BotFileUpdateResponse(
            success=True,
            message="ok",
            file_type="RULES.md",
            entity_type="staff",
            entity_id="u1",
            bot_id="bot-x",
            file_path="identity/RULES.md",
        )
        self._raise_on_get = raise_on_get
        self._raise_on_update = raise_on_update
        self.last_get_call: dict = {}
        self.last_update_call: dict = {}

    async def get_bot_file(
        self,
        entity_type,
        entity_id,
        bot_id,
        file_type,
        operator_id,
        publish_id=None,
        engine_type=None,
    ):
        self.last_get_call = {
            "entity_type": entity_type,
            "entity_id": entity_id,
            "bot_id": bot_id,
            "file_type": file_type,
            "operator_id": operator_id,
            "publish_id": publish_id,
            "engine_type": engine_type,
        }
        if self._raise_on_get:
            raise self._raise_on_get
        # Echo the file_path keyed to the requested file_type (params already
        # carry the physical ``<type>.md`` form), so handler-mapped file_path
        # tracks the request rather than the stub's default RULES.
        return _BotFileResponse(
            **{**vars(self._get_resp), "file_path": f"identity/{file_type}"}
        )

    async def update_bot_file(
        self,
        entity_type,
        entity_id,
        bot_id,
        file_type,
        content,
        operator_id,
        engine_type=None,
    ):
        self.last_update_call = {
            "entity_type": entity_type,
            "entity_id": entity_id,
            "bot_id": bot_id,
            "file_type": file_type,
            "content": content,
            "operator_id": operator_id,
            "engine_type": engine_type,
        }
        if self._raise_on_update:
            raise self._raise_on_update
        return _BotFileUpdateResponse(
            **{**vars(self._update_resp), "file_path": f"identity/{file_type}"}
        )


@pytest.mark.asyncio
async def test_get_bot_identity_file_returns_content_and_path():
    service = _StubGetUpdateService()

    env = await get_bot_identity_file(
        bot_id="bot-x",
        file_type=IdentityFileType.RULES,
        principal={"user_id": "u1"},
        identity_service=service,
        request=_request_without_trace(),
    )

    assert isinstance(env, Envelope)
    assert env.code == CODE_OK
    assert env.message == "OK"
    assert env.request_id == ""
    assert env.data is not None
    assert isinstance(env.data, IdentityFile)
    assert env.data.type == IdentityFileType.RULES
    assert env.data.bot_id == "bot-x"
    assert env.data.content == "# rules"
    assert env.data.file_path == "identity/RULES.md"
    # validate_file_type requires the ``<type>.md`` form — verify the suffix
    # is threaded through to the service (not the bare enum value).
    assert service.last_get_call["file_type"] == "RULES.md"
    # I2 entity fallback: owner → staff entity
    assert service.last_get_call["entity_type"] == "staff"
    assert service.last_get_call["entity_id"] == "u1"
    assert service.last_get_call["operator_id"] == "u1"
    # I3: publish_id is NOT exposed on the openapi contract (default branch).
    assert service.last_get_call["publish_id"] is None


@pytest.mark.asyncio
async def test_get_bot_identity_file_400_on_value_error():
    service = _StubGetUpdateService(
        raise_on_get=ValueError("Invalid file_type: BOGUS.md")
    )

    with pytest.raises(HTTPException) as exc:
        await get_bot_identity_file(
            bot_id="bot-x",
            file_type=IdentityFileType.RULES,
            principal={"user_id": "u1"},
            identity_service=service,
            request=_request_without_trace(),
        )
    assert exc.value.status_code == 400
    assert "Invalid file_type" in exc.value.detail


@pytest.mark.asyncio
async def test_update_bot_identity_file_returns_ref():
    service = _StubGetUpdateService()

    env = await update_bot_identity_file(
        bot_id="bot-x",
        file_type=IdentityFileType.SOUL,
        body=IdentityFileWrite(content="# my soul"),
        principal={"user_id": "u1"},
        identity_service=service,
        request=_request_without_trace(),
    )

    assert isinstance(env, Envelope)
    assert env.code == CODE_OK
    assert env.data is not None
    assert isinstance(env.data, IdentityFileRef)
    assert env.data.type == IdentityFileType.SOUL
    assert env.data.bot_id == "bot-x"
    assert env.data.file_path == "identity/SOUL.md"
    # The enum value is re-suffixed before forwarding (validate_file_type).
    assert service.last_update_call["file_type"] == "SOUL.md"
    assert service.last_update_call["content"] == "# my soul"
    # I2 entity fallback
    assert service.last_update_call["entity_type"] == "staff"
    assert service.last_update_call["entity_id"] == "u1"
    assert service.last_update_call["operator_id"] == "u1"


@pytest.mark.asyncio
async def test_update_bot_identity_file_400_on_value_error():
    service = _StubGetUpdateService(
        raise_on_update=ValueError("Invalid entity_type: bogus")
    )

    with pytest.raises(HTTPException) as exc:
        await update_bot_identity_file(
            bot_id="bot-x",
            file_type=IdentityFileType.RULES,
            body=IdentityFileWrite(content="x"),
            principal={"user_id": "u1"},
            identity_service=service,
            request=_request_without_trace(),
        )
    assert exc.value.status_code == 400
    assert "Invalid entity_type" in exc.value.detail


@pytest.mark.asyncio
async def test_get_and_update_thread_trace_id():
    service = _StubGetUpdateService()
    request = _request_with_trace("trace-identity-2")

    get_env = await get_bot_identity_file(
        bot_id="bot-x",
        file_type=IdentityFileType.RULES,
        principal={"user_id": "u1"},
        identity_service=service,
        request=request,
    )
    assert get_env.request_id == "trace-identity-2"

    update_env = await update_bot_identity_file(
        bot_id="bot-x",
        file_type=IdentityFileType.RULES,
        body=IdentityFileWrite(content="x"),
        principal={"user_id": "u1"},
        identity_service=service,
        request=request,
    )
    assert update_env.request_id == "trace-identity-2"
