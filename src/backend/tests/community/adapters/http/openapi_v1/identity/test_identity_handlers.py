"""openapi_v1 identity handler unit tests.

Direct handler invocation (退路 B per task spec): bypasses FastAPI's
dependency wiring and supplies a stub service. ``principal`` is ``Any``,
so ``None`` is an acceptable stand-in. ``request=None`` exercises the
"outside-a-request" branch of ``_request_id_from``.
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
    "RULES.md", "OKR.md", "SAFETY.md", "SOUL.md", "OUTPUT.md", "MEMORY.md",
    "IDENTITY.md", "AGENTS.md", "USER.md", "TOOLS.md", "HEARTBEAT.md",
    "BOOTSTRAP.md", "KNOWLEDGE.md", "CLAUDE.md", "GREETING.md", "README.md",
]


class _StubBotRepo:
    def __init__(self, bot=None):
        self._bot = bot

    def get_by_id(self, bot_id):
        return self._bot


class _StubIdentityService:
    """Minimal stub satisfying the IdentityService.list_bot_files seam."""

    def __init__(self, presence):
        # presence: list[(file_type_str_with_dot_md, exists_bool)]
        self._presence = presence
        self.last_call_kwargs: dict = {}

    async def list_bot_files(
        self, entity_type, entity_id, bot_id, owner_id, *, engine_type=None,
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
    repo = _StubBotRepo({"owner_id": "u1", "owner_name": "Alice"})

    env = await list_bot_identity_files(
        bot_id="bot-x",
        principal=None,
        identity_service=service,
        bot_repo=repo,
        request=None,
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
async def test_list_bot_identity_files_falls_back_owner_when_missing():
    service = _StubIdentityService(_all_present())
    repo = _StubBotRepo({"owner_id": None, "owner_name": None})  # → fallback to bot_id

    env = await list_bot_identity_files(
        bot_id="bot-x",
        principal=None,
        identity_service=service,
        bot_repo=repo,
        request=None,
    )

    assert env.code == CODE_OK
    # owner_id and entity_id both fall back to bot_id
    assert service.last_call_kwargs["owner_id"] == "bot-x"
    assert service.last_call_kwargs["entity_id"] == "bot-x"


@pytest.mark.asyncio
async def test_list_bot_identity_files_handles_missing_bot_record():
    service = _StubIdentityService(_all_present())
    repo = _StubBotRepo(None)  # no bot record at all

    env = await list_bot_identity_files(
        bot_id="bot-x",
        principal=None,
        identity_service=service,
        bot_repo=repo,
        request=None,
    )

    assert env.code == CODE_OK
    assert service.last_call_kwargs["owner_id"] == "bot-x"


@pytest.mark.asyncio
async def test_list_bot_identity_files_marks_absent_files_false():
    # Half present, half absent — exists flag must reflect each probe.
    presence = [(ft, i % 2 == 0) for i, ft in enumerate(_ALL_IDENTITY_FILES)]
    service = _StubIdentityService(presence)
    repo = _StubBotRepo({"owner_id": "u1", "owner_name": "Alice"})

    env = await list_bot_identity_files(
        bot_id="bot-x",
        principal=None,
        identity_service=service,
        bot_repo=repo,
        request=None,
    )

    assert env.code == CODE_OK
    assert len(env.data.files) == 16
    assert env.data.files[0].exists is True
    assert env.data.files[1].exists is False


@pytest.mark.asyncio
async def test_list_bot_identity_files_reads_x_trace_id_from_request():
    service = _StubIdentityService(_all_present())
    repo = _StubBotRepo({"owner_id": "u1", "owner_name": "Alice"})
    request = SimpleNamespace(headers={"x-trace-id": "trace-identity-1"})

    env = await list_bot_identity_files(
        bot_id="bot-x",
        principal=None,
        identity_service=service,
        bot_repo=repo,
        request=request,
    )

    assert env.request_id == "trace-identity-1"


@pytest.mark.asyncio
async def test_list_bot_identity_files_400_when_entity_type_invalid():
    # Service raises ValueError (invalid entity_type) → handler maps to 400.
    from fastapi import HTTPException

    class _RaisingService:
        async def list_bot_files(self, entity_type, entity_id, bot_id, owner_id, *, engine_type=None):
            raise ValueError(f"Invalid entity_type: {entity_type}")

    service = _RaisingService()
    repo = _StubBotRepo({"owner_id": "u1", "owner_name": "Alice"})

    with pytest.raises(HTTPException) as exc:
        await list_bot_identity_files(
            bot_id="bot-x",
            principal=None,
            identity_service=service,
            bot_repo=repo,
            request=None,
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

    def __init__(self, *, get_resp=None, update_resp=None, raise_on_get=None, raise_on_update=None):
        self._get_resp = get_resp or _BotFileResponse(
            success=True, file_type="RULES.md", entity_type="staff",
            entity_id="u1", bot_id="bot-x", content="# rules", file_path="identity/RULES.md",
        )
        self._update_resp = update_resp or _BotFileUpdateResponse(
            success=True, message="ok", file_type="RULES.md", entity_type="staff",
            entity_id="u1", bot_id="bot-x", file_path="identity/RULES.md",
        )
        self._raise_on_get = raise_on_get
        self._raise_on_update = raise_on_update
        self.last_get_call: dict = {}
        self.last_update_call: dict = {}

    async def get_bot_file(self, entity_type, entity_id, bot_id, file_type, operator_id,
                           publish_id=None, engine_type=None):
        self.last_get_call = {
            "entity_type": entity_type, "entity_id": entity_id, "bot_id": bot_id,
            "file_type": file_type, "operator_id": operator_id,
            "publish_id": publish_id, "engine_type": engine_type,
        }
        if self._raise_on_get:
            raise self._raise_on_get
        # Echo the file_path keyed to the requested file_type (params already
        # carry the physical ``<type>.md`` form), so handler-mapped file_path
        # tracks the request rather than the stub's default RULES.
        return _BotFileResponse(
            **{**vars(self._get_resp), "file_path": f"identity/{file_type}"}
        )

    async def update_bot_file(self, entity_type, entity_id, bot_id, file_type, content,
                              operator_id, engine_type=None):
        self.last_update_call = {
            "entity_type": entity_type, "entity_id": entity_id, "bot_id": bot_id,
            "file_type": file_type, "content": content, "operator_id": operator_id,
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
    repo = _StubBotRepo({"owner_id": "u1", "owner_name": "Alice"})

    env = await get_bot_identity_file(
        bot_id="bot-x",
        file_type=IdentityFileType.RULES,
        principal=None,
        identity_service=service,
        bot_repo=repo,
        request=None,
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
    service = _StubGetUpdateService(raise_on_get=ValueError("Invalid file_type: BOGUS.md"))
    repo = _StubBotRepo({"owner_id": "u1", "owner_name": "Alice"})

    with pytest.raises(HTTPException) as exc:
        await get_bot_identity_file(
            bot_id="bot-x",
            file_type=IdentityFileType.RULES,
            principal=None,
            identity_service=service,
            bot_repo=repo,
            request=None,
        )
    assert exc.value.status_code == 400
    assert "Invalid file_type" in exc.value.detail


@pytest.mark.asyncio
async def test_update_bot_identity_file_returns_ref():
    service = _StubGetUpdateService()
    repo = _StubBotRepo({"owner_id": "u1", "owner_name": "Alice"})

    env = await update_bot_identity_file(
        bot_id="bot-x",
        file_type=IdentityFileType.SOUL,
        body=IdentityFileWrite(content="# my soul"),
        principal=None,
        identity_service=service,
        bot_repo=repo,
        request=None,
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
    service = _StubGetUpdateService(raise_on_update=ValueError("Invalid entity_type: bogus"))
    repo = _StubBotRepo({"owner_id": "u1", "owner_name": "Alice"})

    with pytest.raises(HTTPException) as exc:
        await update_bot_identity_file(
            bot_id="bot-x",
            file_type=IdentityFileType.RULES,
            body=IdentityFileWrite(content="x"),
            principal=None,
            identity_service=service,
            bot_repo=repo,
            request=None,
        )
    assert exc.value.status_code == 400
    assert "Invalid entity_type" in exc.value.detail


@pytest.mark.asyncio
async def test_get_and_update_thread_x_trace_id():
    service = _StubGetUpdateService()
    repo = _StubBotRepo({"owner_id": "u1", "owner_name": "Alice"})
    request = SimpleNamespace(headers={"x-trace-id": "trace-identity-2"})

    get_env = await get_bot_identity_file(
        bot_id="bot-x", file_type=IdentityFileType.RULES, principal=None,
        identity_service=service, bot_repo=repo, request=request,
    )
    assert get_env.request_id == "trace-identity-2"

    update_env = await update_bot_identity_file(
        bot_id="bot-x", file_type=IdentityFileType.RULES,
        body=IdentityFileWrite(content="x"), principal=None,
        identity_service=service, bot_repo=repo, request=request,
    )
    assert update_env.request_id == "trace-identity-2"
