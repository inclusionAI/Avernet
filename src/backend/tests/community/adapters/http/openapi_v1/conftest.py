"""Shared assembled-application fixtures for OpenAPI v1 adapter tests."""

from urllib.parse import parse_qsl, urlsplit, urlunsplit

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient

from agentclaw.community.adapters.http.openapi_v1.errors import (
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
        _grant_not_resolvable_handler,
        _principal_error_handler,
        _user_id_mismatch_handler,
        _validation_error_handler,
    )

    app.add_exception_handler(MissingPrincipalError, _principal_error_handler)
    app.add_exception_handler(UserIdMismatchError, _user_id_mismatch_handler)
    app.add_exception_handler(GrantNotResolvableError, _grant_not_resolvable_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
    return app
