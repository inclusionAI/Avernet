"""Shared assembled-application fixtures for OpenAPI v1 adapter tests."""

from urllib.parse import parse_qsl, urlsplit, urlunsplit

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient

from agentclaw.community.adapters.http.openapi_v1.errors import (
    BotAccessRefusedError,
    BotEditLockError,
    GrantNotResolvableError,
    MissingPrincipalError,
    UserIdMismatchError,
)
from agentclaw.community.adapters.http.openapi_v1.principal import USER_ID_QUERY
from tests.community.framework.fixtures import app_with_testing_modules  # noqa: F401
from tests.community.framework.fixtures import world  # noqa: F401


def user_scoped_client(app: FastAPI, user_id: str, **kwargs) -> TestClient:
    """A ``TestClient`` that names ``user_id`` on every request.

    Every user-scoped public operation now requires the parameter, so without
    this each of ~230 call sites would have to grow one. It is a wrapper rather
    than httpx's own ``Client.params`` because that field does not do what it
    looks like: **it replaces a URL's query string instead of merging with it.**
    ``client.get("/openapi/v1/bots/skills?bot_id=b-1")`` on a client with
    ``params={"user_id": …}`` silently loses ``bot_id``, and the tests that
    caught it would have failed for a reason nowhere near the cause. A per-call
    ``params=`` has the same behaviour.

    So the inline query and any per-call ``params`` are merged here — and the
    merge has to be as lossless as the bug it replaces, or this helper
    reintroduces the same silence on the one path every call site now takes:

    - ``keep_blank_values=True``, so ``?keyword=`` stays an empty string rather
      than disappearing and turning a "passed through as empty" test into an
      unfiltered one that passes for the wrong reason;
    - repeated keys are kept as a list, so ``?tag=a&tag=b`` does not collapse
      to the last one.

    ``user_id`` is only defaulted, so a test can pass its own to assert the 403.
    Passing ``user_id=None`` explicitly **omits** it — the only way to exercise
    an operation that takes none.
    """
    client = TestClient(app, **kwargs)
    inner = client.request

    def request(method: str, url, **kw):
        parts = urlsplit(str(url))
        merged: dict[str, object] = {}
        for key, value in parse_qsl(parts.query, keep_blank_values=True):
            if key in merged:
                existing = merged[key]
                merged[key] = [*existing, value] if isinstance(existing, list) else [
                    existing,
                    value,
                ]
            else:
                merged[key] = value
        merged.update(kw.pop("params", None) or {})
        merged.setdefault(USER_ID_QUERY, user_id)
        kw["params"] = {k: v for k, v in merged.items() if v is not None}
        return inner(method, urlunsplit(parts._replace(query="")), **kw)

    client.request = request  # type: ignore[method-assign]
    return client


def mount_public_error_handlers(app: FastAPI) -> FastAPI:
    """Answer pre-handler failures the way the assembled application does.

    The caller-identity dependencies raise *before* the handler runs, so
    ``@envelope_errors`` never sees them and the real app answers them from
    handlers registered in ``app.py``. A test that mounts one group's router on
    a bare ``FastAPI()`` has neither, and would watch the exception propagate
    instead of the status the surface actually returns.

    The real handlers are imported rather than re-implemented — the same
    convention ``test_principal_seam.py`` follows — so deleting the wiring in
    ``app.py`` fails these tests instead of leaving them green against a local
    copy of it. The import is function-local because importing that module
    builds the whole application.
    """
    from agentclaw.community.adapters.http.app import (
        _bot_access_refused_handler,
        _grant_not_resolvable_handler,
        _principal_error_handler,
        _unhandled_exception_handler,
        _user_id_mismatch_handler,
        _validation_error_handler,
    )

    app.add_exception_handler(MissingPrincipalError, _principal_error_handler)
    app.add_exception_handler(UserIdMismatchError, _user_id_mismatch_handler)
    app.add_exception_handler(GrantNotResolvableError, _grant_not_resolvable_handler)
    app.add_exception_handler(BotAccessRefusedError, _bot_access_refused_handler)
    app.add_exception_handler(BotEditLockError, _unhandled_exception_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
    return app


def is_current(operation: dict) -> bool:
    """Whether a published operation is part of the *current* contract.

    The surface serves two contracts at once while callers migrate: the
    addresses this API has, and the addresses it had. Both are real, both are
    documented, and a test has to say which one it means.

    Convention tests mean the current one — a rule that bound the retiring
    addresses too could only be satisfied by never having changed anything.
    Safety tests mean both, and say so by not calling this.
    """
    return not operation.get("deprecated", False)


def current_operations(document: dict):
    """``(method, path, operation)`` for every non-deprecated operation."""
    for path, item in (document.get("paths") or {}).items():
        for method, operation in item.items():
            if not isinstance(operation, dict) or "responses" not in operation:
                continue
            if is_current(operation):
                yield method, path, operation


class SeamBots:
    """A bot repository for which the addressed bot always exists.

    The seam resolves a level by loading the bot and then asking the
    collaborator service about the caller. A group's own router tests are not
    about either question — they mount one router to assert what a handler does
    *once admitted* — so this double answers "yes, under that owner" and lets
    the level fall out of ``SeamCollaborators``.

    Permissive on purpose, and safe to be: no refusal is asserted through it.
    Whether a caller is admitted at all is ``test_bot_access.py``'s subject,
    against its own doubles, and whether an operation carries the gate is
    ``test_authorization_inventory.py``'s. What would be unsafe is the reverse —
    a router test that *did* assert a refusal against a double it also owns,
    which would pass whatever the seam did.
    """

    def __init__(self, owner_id: str = "actor") -> None:
        self.owner_id = owner_id

    def get_by_id_and_owner(self, bot_id: str, owner_id: str):
        return {"id": 1, "bot_id": bot_id, "owner_id": owner_id, "env": "dev"}

    def get_by_id(self, bot_id: str):
        return {
            "id": 1,
            "bot_id": bot_id,
            "owner_id": self.owner_id,
            "env": "dev",
        }


class SeamCollaborators:
    """The level every non-owner caller holds. Owners never reach this.

    ``resolve_operable_permission_level`` short-circuits ``user_id ==
    owner_id`` to ``OWNER`` before it consults a collaborator service at all,
    so a test whose caller owns the bot gets ``OWNER`` regardless of what this
    is constructed with.
    """

    def __init__(self, level=None) -> None:
        from agentclaw.community.core.bot_collaborator.models import PermissionLevel

        self.level = PermissionLevel.NONE if level is None else level

    def get_operable_permission_level(self, *, bot, user_id, env=None):
        return self.level


class SeamAudit:
    """Collects the rows the seam writes, so a test can read them."""

    def __init__(self) -> None:
        self.rows: list[dict] = []

    def insert(self, data):
        self.rows.append(data)
        return data


class SeamLocks:
    """A lock service for handler tests whose Bot has no collaborators."""

    def get_lock_info(self, **kwargs):
        from agentclaw.community.core.bot_collaborator.models import (  # noqa: PLC0415
            LockInfoResult,
        )

        return LockInfoResult(
            lock=None,
            holder_name=None,
            has_collaborators=False,
            is_owner=False,
        )


def bind_edit_lock_seam(binder, *, locks=None):
    """Wire the no-collaborator lock used by focused handler test apps."""
    from injector import InstanceProvider  # noqa: PLC0415

    from agentclaw.community.api.collaborator_lock_service import (  # noqa: PLC0415
        CollaboratorLockServiceProtocol,
    )

    binder.bind(
        CollaboratorLockServiceProtocol,
        to=InstanceProvider(locks or SeamLocks()),
    )


def bind_bot_access_seam(
    binder, *, bots=None, collaborators=None, audit=None, locks=None
):
    """Wire the three services ``bot_access`` needs, on a bare-``FastAPI`` app.

    Once a group's rows are ``Check``, ``PublicAPIRoute`` attaches the gate to
    every one of its routes, and the gate fails **closed** when the repository
    or the collaborator service is unbound — which is what a hand-built test app
    is. Without this the whole group's router tests answer 404 for a caller who
    owns the bot, and the failure names the seam rather than the test's wiring.

    ``InstanceProvider`` rather than ``to=obj`` because injector
    isinstance-checks a bare instance and these protocols are not all
    ``@runtime_checkable`` — the same reason ``test_bot_access.py`` does it.
    """
    from injector import InstanceProvider  # noqa: PLC0415

    from agentclaw.community.core.repository.protocols.bot import (  # noqa: PLC0415
        BotCollabLogRepositoryProtocol,
        BotRepository,
    )
    from agentclaw.community.core.bot_collaborator.protocols import (  # noqa: PLC0415
        CollaboratorServiceProtocol,
    )
    from agentclaw.community.api.collaborator_lock_service import (  # noqa: PLC0415
        CollaboratorLockServiceProtocol,
    )

    binder.bind(BotRepository, to=InstanceProvider(bots or SeamBots()))
    binder.bind(
        CollaboratorServiceProtocol,
        to=InstanceProvider(collaborators or SeamCollaborators()),
    )
    binder.bind(
        BotCollabLogRepositoryProtocol, to=InstanceProvider(audit or SeamAudit())
    )
    binder.bind(
        CollaboratorLockServiceProtocol,
        to=InstanceProvider(locks or SeamLocks()),
    )
