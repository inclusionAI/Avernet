"""Endpoint-framework coverage for the public CLI-tools operations (W9, #1477).

Three routes, exercised through the assembled public app rather than a mocked
router: the real gateway-principal verification, the collaborator bars, the DI
graph and the repository round trip.

The delivery port and the fetch pipeline are the parts these cases deliberately
do *not* reach — a bot with no binding has no engine to install into, and a
source URL is not fetched in a test. What is on the wire here is the surface:
who may call it, what a refused declaration answers, and that a listing reads
the platform's own table. The pipeline itself is pinned in the service's suite.
"""

from __future__ import annotations

import hashlib
import time

import jwt

from agentclaw.community.adapters.http.openapi_v1.dependencies import PRINCIPAL_HEADER
from agentclaw.community.core.bot_config_manifest.apply.entry_fetch import EntryFetcher
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.repository.protocols.bot.cli_tool import (
    BotCliToolRepositoryProtocol,
)
from agentclaw.community.utils.env_utils import get_current_env
from agentclaw.community.utils.gateway_principal_config import (
    init_principal_verifier_config,
)
from tests.community.factories.access import make_staff_user
from tests.community.factories.bot_collaborator import make_collaborator
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    bind_overrides,
    endpoint_test,
)

_OWNER = "cli-tools-owner"
_BOT_ID = "cli-tools-bot"
_DESKTOP_BOT_ID = "cli-tools-desktop-bot"
_SHARED_BOT_ID = "cli-tools-shared-bot"
_TECLAW_BOT_ID = "cli-tools-teclaw-bot"
_MEMBER = "cli-tools-member"
_KEY = "cli-tools-framework-signing-key-at-least-32-bytes"

#: A minimal little-endian x86-64 ELF header, which is what the platform
#: verifies before it will distribute an executable.
_ELF = bytes(bytearray(b"\x7fELF\x02\x01\x01" + b"\x00" * 11 + b"\x3e\x00")) + b"\x00" * 64
_DIGEST = "sha256:" + hashlib.sha256(_ELF).hexdigest()


class _Secret:
    secret_user = "test"
    secret_value = _KEY


class _Resolver:
    def get_secret(self, _secret_name: str) -> _Secret:
        return _Secret()


def _principal(user_id: str = _OWNER) -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "iss": "gateway",
            "aud": "backend",
            "iat": now,
            "exp": now + 3600,
            "principals": [
                {
                    "type": "user",
                    "subject": {"id": user_id, "username": f"{user_id}@example.test"},
                }
            ],
        },
        _KEY,
        algorithm="HS256",
    )


_HEADERS = {PRINCIPAL_HEADER: _principal()}
_QUERY = {"user_id": _OWNER}
_MEMBER_HEADERS = {PRINCIPAL_HEADER: _principal(_MEMBER)}
#: ``user_id`` is the caller; ``owner_id`` names whose bot is addressed.
_MEMBER_QUERY = {"user_id": _MEMBER, "owner_id": _OWNER}


def _install_body(name: str = "mycli", **overrides) -> dict:
    body = {"name": name, "source": "https://cdn.example.test/mycli", "digest": _DIGEST}
    body.update(overrides)
    return body


def _insert_bot(
    world, *, bot_id: str, engine: str = "openclaw", bot_type: str = "personal"
) -> None:
    world.get(BotRepository).insert(
        {
            "bot_id": bot_id,
            "bot_name": "CLI Tools Bot",
            "owner_id": _OWNER,
            "owner_name": _OWNER,
            "entity_id": _OWNER,
            "entity_type": "staff",
            "creator_id": _OWNER,
            "status": "ACTIVE",
            "active_engine": engine,
            "bot_type": bot_type,
        }
    )


def _install_row(world, *, bot_id: str, name: str, installed_by: str = _OWNER) -> None:
    """A row written straight to the table.

    The install *pipeline* needs a reachable source and a bound device; what
    these cases are about is the surface over the platform's record, so the
    record is seeded directly — the same shortcut the manifest endpoint suite
    takes with a stored document.
    """
    world.get(BotCliToolRepositoryProtocol).upsert(
        env=get_current_env(),
        entity_id=_OWNER,
        bot_id=bot_id,
        name=name,
        source="https://cdn.example.test/mycli",
        digest=_DIGEST,
        subpath=None,
        md5="9f2c4a1b6d8e0f3a5c7b9d1e3a5c7b9d",
        size_bytes=8123456,
        version="1.4.2",
        oss_key=f"teclaw/dev/bolt_data/staff_{_OWNER}/{bot_id}_cli/{name}",
        installed_by=installed_by,
        modifier=_OWNER,
    )


def _seed_bot(world) -> None:
    init_principal_verifier_config(_Resolver(), "test-key", strict=False)
    _insert_bot(world, bot_id=_BOT_ID)


def _seed_bot_with_a_tool(world) -> None:
    _seed_bot(world)
    _install_row(world, bot_id=_BOT_ID, name="mycli")


def _seed_bot_with_two_tools(world) -> None:
    _seed_bot(world)
    _install_row(world, bot_id=_BOT_ID, name="zeta")
    _install_row(world, bot_id=_BOT_ID, name="alpha", installed_by="manifest")


def _seed_desktop_bot(world) -> None:
    init_principal_verifier_config(_Resolver(), "test-key", strict=False)
    _insert_bot(world, bot_id=_DESKTOP_BOT_ID, bot_type="desktop")


def _seed_member(world) -> None:
    """A shared service bot carrying a tool, plus a MEMBER collaborator."""
    init_principal_verifier_config(_Resolver(), "test-key", strict=False)
    _insert_bot(world, bot_id=_SHARED_BOT_ID, bot_type="service")
    _install_row(world, bot_id=_SHARED_BOT_ID, name="mycli")
    make_staff_user(world, user_id=_MEMBER)
    make_collaborator(
        world,
        bot_id=_SHARED_BOT_ID,
        owner_id=_OWNER,
        user_id=_MEMBER,
        role="member",
        operator_id=_OWNER,
    )


def _seed_no_bot(world) -> None:
    init_principal_verifier_config(_Resolver(), "test-key", strict=False)


class _Fetched:
    """What ``EntryFetcher.fetch`` hands back, for a source no test can reach."""

    def __init__(self, content: bytes, digest: str) -> None:
        self.content = content
        self.digest = digest
        self.from_store = False
        self.fallback_reason = None
        self.source_url = None


def _seed_teclaw_bot_with_a_reachable_source(world) -> None:
    """A teclaw bot, and a fetch that answers with a real ELF header.

    Two things are stood in for, and only two. The **fetch** is stubbed because
    no test can reach a source URL — the guarded transport is exercised by its
    own suite. The **engine** is not stubbed at all: on teclaw the composed
    artifact is the delivery, so ``install`` genuinely makes no engine call, and
    this case runs the real service, the real ELF verification, the real object
    write and the real row.
    """
    init_principal_verifier_config(_Resolver(), "test-key", strict=False)
    _insert_bot(world, bot_id=_TECLAW_BOT_ID, engine="teclaw")

    def _fetch(self, ctx, **kwargs):
        return _Fetched(_ELF, kwargs.get("digest") or _DIGEST)

    bind_overrides(world, EntryFetcher, {"fetch": _fetch})


def _seed_teclaw_bot_with_a_tool(world) -> None:
    init_principal_verifier_config(_Resolver(), "test-key", strict=False)
    _insert_bot(world, bot_id=_TECLAW_BOT_ID, engine="teclaw")
    _install_row(world, bot_id=_TECLAW_BOT_ID, name="mycli")


# ── GET ────────────────────────────────────────────────────────────────────


@endpoint_test(
    method="GET",
    path="/openapi/v1/bots/{bot_id}/cli-tools",
    scenario="lists_the_platforms_own_record",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID}, query_params=_QUERY, headers=_HEADERS
    ),
    seed=_seed_bot_with_two_tools,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {
                "tools": [
                    {
                        "name": "alpha",
                        "version": "1.4.2",
                        "digest": _DIGEST,
                        "installed_by": "manifest",
                    },
                    {"name": "zeta", "installed_by": _OWNER},
                ]
            },
        },
    ),
)
def listing_reads_the_table_in_name_order():
    """Name order, not insertion order — a report, an artifact's ref list and
    this response must see the same sequence for the same state.

    ``installed_by`` is the field that makes a full override honest: it says
    which of these a manifest apply put there and which a person did."""


@endpoint_test(
    method="GET",
    path="/openapi/v1/bots/{bot_id}/cli-tools",
    scenario="a_bot_with_no_tools_lists_empty",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID}, query_params=_QUERY, headers=_HEADERS
    ),
    seed=_seed_bot,
    expect=ExpectSuccess(status=200, json_contains={"data": {"tools": []}}),
)
def a_bot_with_no_tools_is_not_a_404():
    """"Has none" and "no such bot" must stay distinguishable."""


@endpoint_test(
    method="GET",
    path="/openapi/v1/bots/{bot_id}/cli-tools",
    scenario="a_member_may_read",
    input=CaseInput(
        path_params={"bot_id": _SHARED_BOT_ID},
        query_params=_MEMBER_QUERY,
        headers=_MEMBER_HEADERS,
    ),
    seed=_seed_member,
    expect=ExpectSuccess(
        status=200, json_contains={"data": {"tools": [{"name": "mycli"}]}}
    ),
)
def a_member_may_read_a_shared_bots_tools():
    """MEMBER to read: knowing what a bot has is part of working on it."""


@endpoint_test(
    method="GET",
    path="/openapi/v1/bots/{bot_id}/cli-tools",
    scenario="an_unknown_bot_is_refused",
    input=CaseInput(
        path_params={"bot_id": "no-such-bot"}, query_params=_QUERY, headers=_HEADERS
    ),
    seed=_seed_no_bot,
    expect=ExpectError(status=404),
)
def an_unknown_bot_is_refused():
    """The bot lookup is the ownership guard as well as the address."""


# ── POST ───────────────────────────────────────────────────────────────────


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/{bot_id}/cli-tools",
    scenario="installs_a_tool_and_records_what_it_verified",
    input=CaseInput(
        path_params={"bot_id": _TECLAW_BOT_ID},
        query_params=_QUERY,
        headers=_HEADERS,
        json_body=_install_body(version="1.4.2"),
    ),
    seed=_seed_teclaw_bot_with_a_reachable_source,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {
                "name": "mycli",
                "version": "1.4.2",
                "digest": _DIGEST,
                "size_bytes": len(_ELF),
                # From the principal, never the body.
                "installed_by": _OWNER,
            },
        },
    ),
)
def install_records_the_platforms_own_md5_and_size():
    """The response is the row, and the row is what the platform verified.

    ``size_bytes`` is the delivered executable's, not the request's — there is
    no byte count in the body to echo. ``installed_by`` is the acting caller,
    which is what lets a later manifest apply's report say it replaced a tool a
    person installed."""


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/{bot_id}/cli-tools",
    scenario="a_duplicate_name_is_409",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID},
        query_params=_QUERY,
        headers=_HEADERS,
        json_body=_install_body(),
    ),
    seed=_seed_bot_with_a_tool,
    expect=ExpectError(status=409),
)
def a_duplicate_name_is_409_not_a_silent_replacement():
    """A manifest apply replaces, because a full override is its declared
    semantics. A single install is not, and overwriting a tool the caller did
    not mention would be the surprising reading of the word."""


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/{bot_id}/cli-tools",
    scenario="a_declaration_without_a_digest_is_refused",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID},
        query_params=_QUERY,
        headers=_HEADERS,
        json_body={"name": "mycli", "source": "https://cdn.example.test/mycli"},
    ),
    seed=_seed_bot,
    expect=ExpectError(status=422),
)
def an_unpinned_executable_is_refused_at_the_edge():
    """``digest`` is required by the request model itself, so the platform never
    reaches the fetch for an unpinned executable."""


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/{bot_id}/cli-tools",
    scenario="a_desktop_bot_cannot_take_cli_tools",
    input=CaseInput(
        path_params={"bot_id": _DESKTOP_BOT_ID},
        query_params=_QUERY,
        headers=_HEADERS,
        json_body=_install_body(),
    ),
    seed=_seed_desktop_bot,
    expect=ExpectError(status=409),
)
def an_engine_that_cannot_take_tools_is_refused_before_the_fetch():
    """The capability answer is re-asked here rather than trusted from a
    manifest ``PUT``: this surface has no stored document to have been
    validated against, and a bot's engine can change."""


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/{bot_id}/cli-tools",
    scenario="a_member_may_not_write",
    input=CaseInput(
        path_params={"bot_id": _SHARED_BOT_ID},
        query_params=_MEMBER_QUERY,
        headers=_MEMBER_HEADERS,
        json_body=_install_body("other"),
    ),
    seed=_seed_member,
    expect=ExpectError(status=404, json_contains={"data": None}),
)
def a_member_may_not_install_an_executable():
    """ADMIN to write. Installing an executable on someone else's bot is not
    something reading access should buy.

    The refusal is a masked 404, byte-identical to a bot that does not exist —
    the same shape the config-manifest write takes, and for the same reason:
    anything finer would confirm the bot to a caller who may not reach it."""


# ── DELETE ─────────────────────────────────────────────────────────────────


@endpoint_test(
    method="DELETE",
    path="/openapi/v1/bots/{bot_id}/cli-tools/{name}",
    scenario="removes_an_installed_tool",
    input=CaseInput(
        path_params={"bot_id": _TECLAW_BOT_ID, "name": "mycli"},
        query_params=_QUERY,
        headers=_HEADERS,
    ),
    seed=_seed_teclaw_bot_with_a_tool,
    expect=ExpectSuccess(status=200, json_contains={"code": 200000}),
)
def delete_removes_the_tool_the_row_and_the_bytes():
    """One call removes all three. On teclaw the engine is not asked — the next
    composed artifact simply stops carrying the ref."""


@endpoint_test(
    method="DELETE",
    path="/openapi/v1/bots/{bot_id}/cli-tools/{name}",
    scenario="removing_a_tool_the_bot_does_not_have_is_404",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID, "name": "ghost"},
        query_params=_QUERY,
        headers=_HEADERS,
    ),
    seed=_seed_bot,
    expect=ExpectError(status=404),
)
def removing_an_absent_tool_is_404_not_idempotent_success():
    """Unlike clearing a manifest, this is not idempotent: "the tool is gone"
    and "you named the wrong tool" are worth telling apart."""


@endpoint_test(
    method="DELETE",
    path="/openapi/v1/bots/{bot_id}/cli-tools/{name}",
    scenario="a_member_may_not_delete",
    input=CaseInput(
        path_params={"bot_id": _SHARED_BOT_ID, "name": "mycli"},
        query_params=_MEMBER_QUERY,
        headers=_MEMBER_HEADERS,
    ),
    seed=_seed_member,
    expect=ExpectError(status=404, json_contains={"data": None}),
)
def a_member_may_not_remove_a_tool():
    """The same ADMIN bar, and the same masked refusal, as installing one."""
