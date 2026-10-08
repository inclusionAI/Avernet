"""Endpoint-framework coverage for the public CLI-tools operations (W9, #1477).

Three routes, exercised through the assembled public app rather than a mocked
router: the real gateway-principal verification, the collaborator bars, the DI
graph and the repository round trip.

The install is an **upload**: the tool's bytes ride the request as a multipart
file part, so these cases send real bytes — a real ELF header, a real tar.gz —
and the platform really verifies them. Nothing is stubbed on that road, because
there is nothing left to stub: no source is fetched.

The delivery port is the one part these cases deliberately do *not* reach — a
bot with no binding has no engine to install into — so the install cases run
against a teclaw bot, where the composed artifact *is* the delivery and the
service genuinely makes no engine call.
"""

from __future__ import annotations

import hashlib
import io
import tarfile
import time

import jwt

from agentclaw.community.adapters.http.openapi_v1.dependencies import PRINCIPAL_HEADER
from agentclaw.community.core.bot_config_manifest.fetch.limits import (
    FETCH_ENTRY_LIMITS,
)
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.repository.protocols.bot.cli_tool import (
    BotCliToolRepositoryProtocol,
)
from agentclaw.community.utils.env_utils import get_current_env
from agentclaw.community.utils.gateway_principal_config import (
    init_principal_verifier_config,
)
from tests.community.core.bot_config_manifest.cli_tools._fakes import elf as _elf_header
from tests.community.factories.access import make_staff_user
from tests.community.factories.bot_collaborator import make_collaborator
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)

_OWNER = "cli-tools-owner"
_BOT_ID = "cli-tools-bot"
_DESKTOP_BOT_ID = "cli-tools-desktop-bot"
_SHARED_BOT_ID = "cli-tools-shared-bot"
_TECLAW_BOT_ID = "cli-tools-teclaw-bot"
_MEMBER = "cli-tools-member"
_KEY = "cli-tools-framework-signing-key-at-least-32-bytes"

#: A well-formed 64-bit x86-64 executable header, which is what the platform
#: verifies before it will distribute anything.
_ELF = _elf_header()
_DIGEST = "sha256:" + hashlib.sha256(_ELF).hexdigest()


def _targz(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for member, data in members.items():
            info = tarfile.TarInfo(member)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


_ARCHIVE = _targz({"mycli-1.4.2/bin/mycli": _ELF})
_ARCHIVE_DIGEST = "sha256:" + hashlib.sha256(_ARCHIVE).hexdigest()

#: One megabyte past the category's own per-entry width (schema §5: 200 MiB),
#: read from the table that states it rather than restated as a number here.
_OVER_THE_CAP = FETCH_ENTRY_LIMITS["cli_tools"] + 1024 * 1024


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


def _install_form(name: str = "mycli", **overrides) -> dict:
    """The form half of the upload: everything except the file part."""
    form = {"name": name, "digest": _DIGEST}
    form.update(overrides)
    return form


def _file_part(content: bytes = _ELF, filename: str = "mycli") -> list:
    """The file half, in httpx's ``files=`` shape."""
    return [("file", (filename, content))]


def _upload(
    name: str = "mycli", *, content: bytes = _ELF, bot_id: str = None, **overrides
) -> CaseInput:
    return CaseInput(
        path_params={"bot_id": bot_id or _BOT_ID},
        query_params=_QUERY,
        headers=_HEADERS,
        form_data=_install_form(name, **overrides),
        files=_file_part(content),
    )


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
        source="",
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


def _seed_teclaw_bot(world) -> None:
    """A teclaw bot, and nothing stood in for.

    Nothing needs to be: the bytes come in the request, so there is no fetch to
    stub, and on teclaw the composed artifact *is* the delivery, so ``install``
    genuinely makes no engine call. This case runs the real service, the real
    digest check, the real ELF verification, the real object write and the real
    row.
    """
    init_principal_verifier_config(_Resolver(), "test-key", strict=False)
    _insert_bot(world, bot_id=_TECLAW_BOT_ID, engine="teclaw")


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
    scenario="installs_an_uploaded_binary_and_records_what_it_verified",
    input=_upload(bot_id=_TECLAW_BOT_ID, version="1.4.2"),
    seed=_seed_teclaw_bot,
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
    no byte count in the form to echo. ``installed_by`` is the acting caller,
    which is what lets a later manifest apply's report say it replaced a tool a
    person installed."""


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/{bot_id}/cli-tools",
    scenario="installs_the_subpath_out_of_an_uploaded_archive",
    input=_upload(
        bot_id=_TECLAW_BOT_ID,
        content=_ARCHIVE,
        digest=_ARCHIVE_DIGEST,
        unpack="tar.gz",
        subpath="mycli-1.4.2/bin/mycli",
    ),
    seed=_seed_teclaw_bot,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "data": {
                "name": "mycli",
                "subpath": "mycli-1.4.2/bin/mycli",
                # The archive's, because that is what was uploaded and what
                # ``digest`` pins; ``subpath`` selects the file inside it.
                "digest": _ARCHIVE_DIGEST,
                # The selected member's, not the archive's: the platform
                # computes these after unpacking.
                "size_bytes": len(_ELF),
                "md5": hashlib.md5(_ELF).hexdigest(),
            }
        },
    ),
)
def an_archive_upload_installs_the_member_subpath_names():
    """``digest`` covers what was uploaded; ``md5`` and ``size_bytes`` cover
    what was installed. Reading both off one response is how a caller can tell
    that the platform unpacked rather than distributed the archive."""


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/{bot_id}/cli-tools",
    scenario="bytes_that_do_not_match_the_digest_are_refused",
    input=_upload(
        bot_id=_TECLAW_BOT_ID, digest="sha256:" + "0" * 64
    ),
    seed=_seed_teclaw_bot,
    expect=ExpectError(status=422, json_contains={"code": 422110}),
)
def an_upload_that_does_not_match_its_digest_is_refused():
    """The digest is the client's statement of what it meant to send, so a
    truncated upload or the wrong file is a refusal rather than an executable
    nobody named. Nothing is stored and no row is written."""


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/{bot_id}/cli-tools",
    scenario="unpack_without_a_subpath_is_refused",
    input=_upload(
        bot_id=_TECLAW_BOT_ID,
        content=_ARCHIVE,
        digest=_ARCHIVE_DIGEST,
        unpack="tar.gz",
    ),
    seed=_seed_teclaw_bot,
    expect=ExpectError(status=422, json_contains={"code": 422110}),
)
def an_archive_with_no_subpath_is_refused_rather_than_guessed_at():
    """One entry is one command is one file: with an archive and no ``subpath``
    there is nothing that says which member is the command, and picking one
    would be the platform guessing which executable to install."""


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/{bot_id}/cli-tools",
    scenario="an_upload_over_the_cap_is_refused",
    input=CaseInput(
        path_params={"bot_id": _TECLAW_BOT_ID},
        query_params=_QUERY,
        # A body that announces more than the cap. The bytes behind it are
        # small on purpose: what this case pins is that the *announcement* is
        # enough to be refused, so the platform never reads 200 MiB to find out
        # it did not want them. The other half of the rule — a caller that
        # announces nothing gets cut off mid-stream — is pinned as a unit on
        # the route class, where the stream can be watched.
        headers={**_HEADERS, "content-length": str(_OVER_THE_CAP)},
        form_data=_install_form(),
        files=_file_part(),
    ),
    seed=_seed_teclaw_bot,
    expect=ExpectError(status=413, json_contains={"code": 413110}),
)
def an_upload_past_the_cap_is_refused_while_it_arrives():
    """200 MiB is the width schema §5 gives this category, and the same number
    caps a manifest-declared fetch: a binary the platform would refuse to fetch
    is not one it accepts by upload either."""


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/{bot_id}/cli-tools",
    scenario="a_duplicate_name_is_409",
    input=_upload(),
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
    scenario="an_upload_without_a_digest_is_refused",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID},
        query_params=_QUERY,
        headers=_HEADERS,
        form_data={"name": "mycli"},
        files=_file_part(),
    ),
    seed=_seed_bot,
    expect=ExpectError(status=422),
)
def an_unpinned_executable_is_refused_at_the_edge():
    """``digest`` is required by the form model itself, so the platform never
    reaches the ELF gate for an unpinned executable."""


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/{bot_id}/cli-tools",
    scenario="a_desktop_bot_cannot_take_cli_tools",
    input=_upload(bot_id=_DESKTOP_BOT_ID),
    seed=_seed_desktop_bot,
    expect=ExpectError(status=409),
)
def an_engine_that_cannot_take_tools_is_refused_before_anything_is_stored():
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
        form_data=_install_form("other"),
        files=_file_part(),
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
