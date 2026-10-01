"""The CLI-tool delivery boundary and both families' ports (W9).

The properties pinned here are contract properties, not behaviours of one
engine: every signature is name-addressed, nothing reads bytes back out of a
container, and the ARCA arm reaches the engine through the adapter transport
and no shell.
"""
from __future__ import annotations

import base64
import inspect

import pytest

from agentclaw.community.core.bot_config_manifest.cli_tools.arca_port import (
    REPLACE_PATH,
    REPLACE_TIMEOUT_CAP_SECONDS,
    replace_timeout_for,
    CONTROL_TIMEOUT_SECONDS,
    DELETE_PATH,
    INSTALL_PATH,
    INSTALL_TIMEOUT_SECONDS,
    LIST_PATH,
    ArcaCliToolPort,
)
from agentclaw.community.core.bot_config_manifest.cli_tools.context import (
    CliToolContext,
)
from agentclaw.community.core.bot_config_manifest.cli_tools.delivery_port import (
    CliToolDeliveryError,
    DeliverableCliTool,
    CliToolDeliveryPort,
    CliToolPlacementError,
)
from agentclaw.community.core.bot_config_manifest.cli_tools.teclaw_port import (
    CliToolDriftUnobservableError,
    TeclawCliToolPort,
)
from agentclaw.community.plugin_api.device_adapter_transport import (
    DeviceAdapterEndpointNotFoundError,
    DeviceAdapterHTTPStatusError,
)

from ._fakes import code_of

_CTX = CliToolContext(
    bot_id="bot7",
    owner_id="u1",
    actor_id="u1",
    entity_id="u1",
    env="dev",
    engine_type="openclaw",
    tenant="teamclaw",
)
_BYTES = b"\x7fELF\x02\x01\x01"


# ── fakes ─────────────────────────────────────────────────────────────────


class _Context:
    conn_info = {"url": "http://engine", "headers": {}}


class FakeResolver:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.calls: list[tuple[str, str]] = []
        self.error = error

    def resolve_for_bot(self, bot_id: str, user_id: str):
        self.calls.append((bot_id, user_id))
        if self.error is not None:
            raise self.error
        return _Context()


class FakeTransport:
    """Records every ``invoke``; answers with a configurable envelope."""

    def __init__(self, *, response=None, error: Exception | None = None) -> None:
        self.calls: list[dict] = []
        self.response = response if response is not None else {"success": True}
        self.error = error

    async def invoke(self, conn_info, method, path, body=None, params=None, *, timeout=None):
        self.calls.append(
            {"conn_info": conn_info, "method": method, "path": path,
             "body": body, "timeout": timeout}
        )
        if self.error is not None:
            raise self.error
        return self.response

    async def stream(self, *a, **kw):  # pragma: no cover - unused by the port
        raise NotImplementedError


def _arca(*, resolver=None, transport=None):
    resolver = resolver or FakeResolver()
    transport = transport or FakeTransport()
    return ArcaCliToolPort(resolver=resolver, transport=transport), resolver, transport


# ── the contract ──────────────────────────────────────────────────────────


def test_every_delivery_signature_takes_a_name_never_a_path() -> None:
    """The one field a tool is addressed by promises not to be a path, and
    that promise is only worth anything if no signature offers an alternative."""
    forbidden = {"path", "target_path", "dir", "directory", "dest", "location"}
    for method in ("install", "delete", "list", "replace_all"):
        params = set(inspect.signature(getattr(CliToolDeliveryPort, method)).parameters)
        assert not (params & forbidden), f"{method} takes a path-shaped argument"


def test_the_port_has_no_get() -> None:
    """The platform holds the bytes, so nothing reads them back out of a
    container. A ``get`` would be the door that makes the container a source
    of truth again (spec D-5)."""
    assert not hasattr(CliToolDeliveryPort, "get")
    assert not hasattr(CliToolDeliveryPort, "read")
    assert not hasattr(CliToolDeliveryPort, "download")


def test_a_family_that_forgets_a_method_fails_at_construction() -> None:
    class Incomplete(CliToolDeliveryPort):
        """Deliberately implements nothing."""

    with pytest.raises(TypeError) as excinfo:
        Incomplete()
    assert "abstract" in str(excinfo.value)


def test_replace_all_has_no_default_built_from_the_single_tool_methods() -> None:
    """Rev 8 removed it, and the removal is the point.

    A loop of deletes then installs describes a *journey*, and a journey has
    intermediate states an observer can see: the container would be told it had
    lost tools it was about to regain. A family states the destination instead,
    which is a thing both of ours can do in one call — so a family that does
    not implement it fails at construction rather than silently getting the
    wrong semantics (spec D-13).
    """
    class Incomplete(CliToolDeliveryPort):
        async def install(self, ctx, *, name, data):
            ...

        async def delete(self, ctx, *, name):
            ...

        async def list(self, ctx):
            return []

    with pytest.raises(TypeError):
        Incomplete()


# ── ARCA ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_arca_install_is_one_call_to_the_engines_install_endpoint() -> None:
    port, resolver, transport = _arca()
    await port.install(_CTX, name="mycli", data=_BYTES)
    assert len(transport.calls) == 1
    call = transport.calls[0]
    assert (call["method"], call["path"]) == ("POST", INSTALL_PATH)
    assert call["body"]["name"] == "mycli"
    assert base64.b64decode(call["body"]["content_b64"]) == _BYTES
    assert call["body"]["size_bytes"] == len(_BYTES)
    assert call["timeout"] == INSTALL_TIMEOUT_SECONDS
    assert resolver.calls == [("bot7", "u1")]


@pytest.mark.asyncio
async def test_arca_sends_no_path_and_no_directory_to_the_engine() -> None:
    """Placement is the engine's; a directory in the body would make it the
    platform's again."""
    port, _, transport = _arca()
    await port.install(_CTX, name="mycli", data=_BYTES)
    body = transport.calls[0]["body"]
    assert set(body) == {"name", "size_bytes", "content_b64"}


@pytest.mark.asyncio
async def test_arca_delete_names_the_command_and_nothing_else() -> None:
    port, _, transport = _arca()
    await port.delete(_CTX, name="mycli")
    call = transport.calls[0]
    assert (call["path"], call["body"], call["timeout"]) == (
        DELETE_PATH, {"name": "mycli"}, CONTROL_TIMEOUT_SECONDS,
    )


@pytest.mark.asyncio
async def test_arca_list_reads_the_names_out_of_the_envelope() -> None:
    transport = FakeTransport(
        response={"success": True, "data": {"tools": [{"name": "a"}, "b"]}}
    )
    port, _, _ = _arca(transport=transport)
    assert await port.list(_CTX) == ["a", "b"]
    assert transport.calls[0]["path"] == LIST_PATH


@pytest.mark.asyncio
async def test_arca_list_reads_a_bare_list_envelope_too() -> None:
    port, _, _ = _arca(transport=FakeTransport(response={"data": ["a", "b"]}))
    assert await port.list(_CTX) == ["a", "b"]


@pytest.mark.asyncio
async def test_arca_list_of_an_unparseable_envelope_is_empty_not_an_error() -> None:
    """The listing decides nothing: it feeds a drift comparison. An envelope
    nobody can parse shows up as drift rather than as an exception."""
    port, _, _ = _arca(transport=FakeTransport(response={"weird": True}))
    assert await port.list(_CTX) == []


# ── ARCA failures ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_engine_without_cli_endpoints_fails_honestly() -> None:
    """A 404 here means "this engine build has no CLI endpoints" — never "the
    tool is missing", since the platform never asked about one by path."""
    transport = FakeTransport(error=DeviceAdapterEndpointNotFoundError("404"))
    port, _, _ = _arca(transport=transport)
    with pytest.raises(CliToolPlacementError) as excinfo:
        await port.install(_CTX, name="mycli", data=_BYTES)
    assert "no CLI endpoint" in str(excinfo.value)


@pytest.mark.asyncio
async def test_a_non_2xx_raises_with_the_engines_error() -> None:
    transport = FakeTransport(error=DeviceAdapterHTTPStatusError(500, "disk full"))
    port, _, _ = _arca(transport=transport)
    with pytest.raises(CliToolPlacementError) as excinfo:
        await port.install(_CTX, name="mycli", data=_BYTES)
    assert "disk full" in str(excinfo.value)


@pytest.mark.asyncio
async def test_a_200_reporting_failure_still_raises() -> None:
    """An engine that answers 200 with ``success: false`` has refused, and
    treating that as success is how a tool the bot does not have gets
    recorded as installed."""
    transport = FakeTransport(response={"success": False, "message": "no such arch"})
    port, _, _ = _arca(transport=transport)
    with pytest.raises(CliToolPlacementError) as excinfo:
        await port.install(_CTX, name="mycli", data=_BYTES)
    assert "no such arch" in str(excinfo.value)


@pytest.mark.asyncio
async def test_an_unbound_device_raises_the_ports_own_error() -> None:
    """One failure type reaches the service, whatever went wrong underneath."""
    port, _, _ = _arca(resolver=FakeResolver(error=RuntimeError("no active binding")))
    with pytest.raises(CliToolPlacementError) as excinfo:
        await port.install(_CTX, name="mycli", data=_BYTES)
    assert "no active binding" in str(excinfo.value)


# ── ARCA runs no shell ────────────────────────────────────────────────────


def test_the_arca_port_reaches_no_shell_channel() -> None:
    """The executable bit is part of what the engine's ``install`` means. A
    platform-side ``chmod`` would be a second implementation of the engine's
    job, reached through a general shell channel with a user-supplied name to
    quote into it."""
    from agentclaw.community.core.bot_config_manifest.cli_tools import arca_port

    code = code_of(arca_port)
    for forbidden in (
        "chmod", "exec_command", "execute_baas_shell", "shell_command",
        "subprocess", "run_shell",
    ):
        assert forbidden not in code, f"the ARCA port calls {forbidden!r}"


def test_the_arca_port_names_no_tools_directory() -> None:
    """The directory is the engine's. A copy of it here would be a second
    answer to a question the engine already answers — and a stale one the
    first time an engine moved it."""
    from agentclaw.community.core.bot_config_manifest.cli_tools import arca_port

    assert "/home/admin" not in inspect.getsource(arca_port)
    assert "openclaw/cli" not in inspect.getsource(arca_port)


def test_the_arca_port_takes_only_a_resolver_and_a_transport() -> None:
    params = set(inspect.signature(ArcaCliToolPort.__init__).parameters)
    assert params == {"self", "resolver", "transport"}


# ── ARCA, the whole set ───────────────────────────────────────────────────


def _ok(*names: str) -> dict:
    return {
        "success": True,
        "data": {"results": [{"name": n, "success": True} for n in names]},
    }


@pytest.mark.asyncio
async def test_arca_replace_is_one_call_carrying_the_whole_set() -> None:
    """One round trip, whatever the size — and the destination, not a diff."""
    port, _, transport = _arca(transport=FakeTransport(response=_ok("a", "b")))
    await port.replace_all(
        _CTX,
        [
            DeliverableCliTool(name="a", data=_BYTES),
            DeliverableCliTool(name="b", data=b"\x7fELF-b"),
        ],
    )
    assert len(transport.calls) == 1
    call = transport.calls[0]
    assert (call["method"], call["path"]) == ("POST", REPLACE_PATH)
    assert [t["name"] for t in call["body"]["tools"]] == ["a", "b"]
    assert base64.b64decode(call["body"]["tools"][0]["content_b64"]) == _BYTES
    assert call["timeout"] == replace_timeout_for(2)


@pytest.mark.asyncio
async def test_an_empty_set_is_still_a_call() -> None:
    """``cli_tools: []`` means "this bot has no tools", which the engine has to
    be told — skipping the call would leave every tool it has in place."""
    port, _, transport = _arca(transport=FakeTransport(response=_ok()))
    assert await port.replace_all(_CTX, []) == {}
    assert transport.calls[0]["body"] == {"tools": []}


@pytest.mark.asyncio
async def test_the_replace_budget_scales_with_the_set_but_is_capped() -> None:
    assert replace_timeout_for(1) == INSTALL_TIMEOUT_SECONDS
    assert replace_timeout_for(0) == INSTALL_TIMEOUT_SECONDS
    assert replace_timeout_for(3) == 3 * INSTALL_TIMEOUT_SECONDS
    assert replace_timeout_for(10_000) == REPLACE_TIMEOUT_CAP_SECONDS


@pytest.mark.asyncio
async def test_a_per_name_refusal_comes_back_named() -> None:
    """The whole reason the response is per name: an apply report is per
    declared entry, and one verdict for the set would lose which tool the
    engine actually objected to (spec D-15)."""
    transport = FakeTransport(
        response={
            "success": True,
            "data": {
                "results": [
                    {"name": "a", "success": True},
                    {"name": "b", "success": False, "message": "not executable"},
                ]
            },
        }
    )
    port, _, _ = _arca(transport=transport)
    failures = await port.replace_all(
        _CTX,
        [
            DeliverableCliTool(name="a", data=_BYTES),
            DeliverableCliTool(name="b", data=_BYTES),
        ],
    )
    assert failures == {"b": "not executable"}


@pytest.mark.asyncio
async def test_a_response_that_omits_a_tool_is_not_read_as_success() -> None:
    """Silence is the dangerous answer: taking it for success is exactly how a
    tool the bot does not have ends up in a green apply report."""
    port, _, _ = _arca(transport=FakeTransport(response=_ok("a")))
    with pytest.raises(CliToolDeliveryError):
        await port.replace_all(
            _CTX,
            [
                DeliverableCliTool(name="a", data=_BYTES),
                DeliverableCliTool(name="b", data=_BYTES),
            ],
        )


@pytest.mark.asyncio
async def test_a_response_with_no_results_at_all_is_refused() -> None:
    port, _, _ = _arca(transport=FakeTransport(response={"success": True}))
    with pytest.raises(CliToolDeliveryError):
        await port.replace_all(_CTX, [DeliverableCliTool(name="a", data=_BYTES)])


@pytest.mark.asyncio
async def test_a_name_the_platform_did_not_send_is_ignored() -> None:
    """An engine listing something extra is drift for ``list`` to surface, not
    a failure of this call."""
    port, _, _ = _arca(transport=FakeTransport(response=_ok("a", "stowaway")))
    assert await port.replace_all(_CTX, [DeliverableCliTool(name="a", data=_BYTES)]) == {}


# ── teclaw ────────────────────────────────────────────────────────────────


def test_the_teclaw_port_holds_no_engine_collaborator() -> None:
    """The strongest form of "makes no engine call": there is nothing to call
    with. The port takes no transport, no resolver and no device — only the
    whole-artifact redeliver, which reaches the *container*, never a CLI
    endpoint, and which the apply binding leaves unset."""
    assert vars(TeclawCliToolPort()) == {"_redeliver": None}


@pytest.mark.asyncio
async def test_teclaw_install_and_delete_do_nothing() -> None:
    """The artifact is the delivery: the row and the stored bytes are what
    compose, exactly as ``mcp`` is delivered."""
    port = TeclawCliToolPort()
    await port.install(_CTX, name="mycli", data=_BYTES)
    await port.delete(_CTX, name="mycli")


@pytest.mark.asyncio
async def test_teclaw_replace_all_makes_no_engine_call_either() -> None:
    """And answers no per-name failures: an artifact is accepted whole or it is
    not, so there is no per-tool verdict for this family to report."""
    failures = await TeclawCliToolPort().replace_all(
        _CTX, [DeliverableCliTool(name="mycli", data=_BYTES)]
    )
    assert failures == {}


@pytest.mark.asyncio
async def test_teclaw_does_not_ask_for_the_tools_bytes() -> None:
    """The artifact references the objects rather than carrying them, so
    reading a few hundred megabytes back out of the store to hand this port an
    argument it ignores would be pure waste."""
    assert TeclawCliToolPort.needs_tool_bytes is False


@pytest.mark.asyncio
async def test_teclaw_list_refuses_rather_than_answering_wrongly() -> None:
    """Returning ``[]`` would read as "this bot has no tools" — which is what
    a removal would be computed from."""
    with pytest.raises(CliToolDriftUnobservableError):
        await TeclawCliToolPort().list(_CTX)


def test_the_teclaw_port_composes_no_container_path() -> None:
    from agentclaw.community.core.bot_config_manifest.cli_tools import teclaw_port

    code = code_of(teclaw_port)
    for forbidden in ("/workspace", "/identity", "/home/admin", "os.path", "Path("):
        assert forbidden not in code, f"the teclaw port names {forbidden!r}"
