"""Declarative coverage for the retiring routines and resources addresses.

The bot-first re-addressing (``specs/2026-08-15-openapi-v1-bot-first-addressing``)
moved ``bot_id`` from a query parameter into a path segment. The old spellings
still answer, marked deprecated, until the 2027-08-15 sunset, and
``deprecated/_requery.py`` builds each one by taking the current handler's
signature and re-annotating that single parameter.

``test_legacy_parity.py`` already drives every retiring address beside its
replacement and asserts the two reach the same decision — but it does so
unauthenticated, so what it compares is a masked refusal. These cases are the
other half: the same operations, authenticated, on the success path.

Reusing the seeds from the current-address modules is the point rather than a
shortcut. The shim is supposed to reach the *same handler* with the *same
dependencies*; seeding it identically and getting the same status is what
demonstrates that, and it cannot drift, because there is only one seed.
"""

from __future__ import annotations

from tests.community.endpoints.test_openapi_resources import _BOT_ID as _RESOURCE_BOT
from tests.community.endpoints.test_openapi_resources import (
    _EXISTING_PATH,
    _NEW_DIR,
    _NEW_PATH,
    _OCTET_HEADERS,
)
from tests.community.endpoints.test_openapi_resources import (
    _HAPPY_CASES as _MODERN_RESOURCE_CASES,
)
from tests.community.endpoints.test_openapi_resources import (
    _HEADERS as _RESOURCE_HEADERS,
)
from tests.community.endpoints.test_openapi_resources import _OWNER as _RESOURCE_OWNER
from tests.community.endpoints.test_openapi_resources import (
    _seed_happy_services as _seed_resources,
)
from tests.community.endpoints.test_openapi_resources import (
    _seed_verifier as _seed_resources_verifier,
)
from tests.community.endpoints.test_openapi_routines import _BOT_ID as _ROUTINE_BOT
from tests.community.endpoints.test_openapi_routines import (
    _CREATE_BODY,
    _ROUTINE_ID,
    _UPDATE_BODY,
)
from tests.community.endpoints.test_openapi_routines import (
    _HAPPY_CASES as _MODERN_ROUTINE_CASES,
)
from tests.community.endpoints.test_openapi_routines import _HEADERS as _ROUTINE_HEADERS
from tests.community.endpoints.test_openapi_routines import _OWNER as _ROUTINE_OWNER
from tests.community.endpoints.test_openapi_routines import (
    _seed_happy_services as _seed_routines,
)
from tests.community.endpoints.test_openapi_routines import (
    _seed_verifier as _seed_routines_verifier,
)
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)

_ROUTINES_BASE = "/openapi/v1/bots/routines"
_RESOURCES_BASE = "/openapi/v1/bots/resources"

#: On a retiring address the bot is named in the query, not on the path.
_ROUTINE_QUERY = {"user_id": _ROUTINE_OWNER, "bot_id": _ROUTINE_BOT}
_ROUTINE_FORBIDDEN = {"user_id": "another-user", "bot_id": _ROUTINE_BOT}
_ROUTINE_PATH_PARAMS = {"routine_id": _ROUTINE_ID}

#: The one retiring routines address that is not a ``_requery`` swap. The old
#: create carried the bot in the *body*, so ``deprecated/routines.py`` hand-writes
#: it as a ``LegacyRoutineSpec`` field rather than moving it to the query.
_LEGACY_CREATE_BODY = {**_CREATE_BODY, "bot_id": _ROUTINE_BOT}


def _resource_query(path: str) -> dict:
    return {"user_id": _RESOURCE_OWNER, "bot_id": _RESOURCE_BOT, "path": path}


def _resource_forbidden(path: str) -> dict:
    return {"user_id": "another-user", "bot_id": _RESOURCE_BOT, "path": path}


# ── routines, at the address they are retiring from ────────────────────
_ROUTINE_CASES = (
    (
        "GET",
        _ROUTINES_BASE,
        CaseInput(query_params=_ROUTINE_QUERY, headers=_ROUTINE_HEADERS),
        200,
    ),
    (
        "POST",
        _ROUTINES_BASE,
        CaseInput(
            query_params=_ROUTINE_QUERY,
            headers=_ROUTINE_HEADERS,
            json_body=_LEGACY_CREATE_BODY,
        ),
        201,
    ),
    (
        "GET",
        f"{_ROUTINES_BASE}/{{routine_id}}",
        CaseInput(
            path_params=_ROUTINE_PATH_PARAMS,
            query_params=_ROUTINE_QUERY,
            headers=_ROUTINE_HEADERS,
        ),
        200,
    ),
    (
        "PATCH",
        f"{_ROUTINES_BASE}/{{routine_id}}",
        CaseInput(
            path_params=_ROUTINE_PATH_PARAMS,
            query_params=_ROUTINE_QUERY,
            headers=_ROUTINE_HEADERS,
            json_body=_UPDATE_BODY,
        ),
        200,
    ),
    (
        "DELETE",
        f"{_ROUTINES_BASE}/{{routine_id}}",
        CaseInput(
            path_params=_ROUTINE_PATH_PARAMS,
            query_params=_ROUTINE_QUERY,
            headers=_ROUTINE_HEADERS,
        ),
        200,
    ),
    (
        "POST",
        f"{_ROUTINES_BASE}/{{routine_id}}/run",
        CaseInput(
            path_params=_ROUTINE_PATH_PARAMS,
            query_params=_ROUTINE_QUERY,
            headers=_ROUTINE_HEADERS,
        ),
        200,
    ),
    (
        "GET",
        f"{_ROUTINES_BASE}/{{routine_id}}/runs",
        CaseInput(
            path_params=_ROUTINE_PATH_PARAMS,
            query_params=_ROUTINE_QUERY,
            headers=_ROUTINE_HEADERS,
        ),
        200,
    ),
)


# ── resources, at the address they are retiring from ───────────────────
_RESOURCE_CASES = (
    (
        "GET",
        _RESOURCES_BASE,
        CaseInput(
            query_params=_resource_query("docs"), headers=_RESOURCE_HEADERS
        ),
        200,
    ),
    (
        "GET",
        f"{_RESOURCES_BASE}/stat",
        CaseInput(
            query_params=_resource_query(_EXISTING_PATH), headers=_RESOURCE_HEADERS
        ),
        200,
    ),
    (
        "GET",
        f"{_RESOURCES_BASE}/download",
        CaseInput(
            query_params=_resource_query(_EXISTING_PATH), headers=_RESOURCE_HEADERS
        ),
        200,
    ),
    (
        "GET",
        f"{_RESOURCES_BASE}/preview",
        CaseInput(
            query_params=_resource_query(_EXISTING_PATH), headers=_RESOURCE_HEADERS
        ),
        200,
    ),
    (
        "POST",
        f"{_RESOURCES_BASE}/upload",
        CaseInput(
            query_params=_resource_query(_NEW_PATH),
            headers=_OCTET_HEADERS,
            raw_body=b"hello world",
        ),
        201,
    ),
    (
        "POST",
        f"{_RESOURCES_BASE}/mkdir",
        CaseInput(
            query_params=_resource_query(_NEW_DIR), headers=_RESOURCE_HEADERS
        ),
        201,
    ),
    (
        "DELETE",
        _RESOURCES_BASE,
        CaseInput(
            query_params=_resource_query(_EXISTING_PATH), headers=_RESOURCE_HEADERS
        ),
        200,
    ),
)


# The retiring address must answer exactly what its replacement answers, so each
# case pins the *same* fragment the modern case pins. That is what distinguishes
# a working shim from one whose signature surgery quietly dropped a field: both
# would return 200, only one returns the right body. The zip is guarded, so a
# reordering of either list fails loudly rather than silently pairing the wrong
# expectation with the wrong address.
for (_method, _path, _input, _status), _modern in zip(
    _ROUTINE_CASES, _MODERN_ROUTINE_CASES, strict=True
):
    assert (_method, _status) == (_modern[0], _modern[3]), (
        f"legacy/modern routine case lists drifted at {_method} {_path}"
    )
    endpoint_test(
        method=_method,
        path=_path,
        scenario="happy",
        input=_input,
        seed=_seed_routines,
        expect=ExpectSuccess(status=_status, json_contains=_modern[4] or {}),
    )(lambda: None)

    endpoint_test(
        method=_method,
        path=_path,
        scenario="forbidden_user_scope",
        input=CaseInput(
            path_params=_input.path_params,
            query_params=_ROUTINE_FORBIDDEN,
            headers=_ROUTINE_HEADERS,
            json_body=_input.json_body,
        ),
        seed=_seed_routines_verifier,
        expect=ExpectError(status=403, json_contains={"data": None}),
    )(lambda: None)


# download-dir is a new operation with no retiring address (the legacy shim
# skips it on purpose), so it is filtered out of this one-to-one pairing; its
# modern address is covered in test_openapi_resources.py itself.
_MODERN_LEGACY_PAIRED = tuple(
    case
    for case in _MODERN_RESOURCE_CASES
    if not case[1].endswith("/download-dir")
)

for (_method, _path, _input, _status), _modern in zip(
    _RESOURCE_CASES, _MODERN_LEGACY_PAIRED, strict=True
):
    assert (_method, _status) == (_modern[0], _modern[3]), (
        f"legacy/modern resource case lists drifted at {_method} {_path}"
    )
    endpoint_test(
        method=_method,
        path=_path,
        scenario="happy",
        input=_input,
        seed=_seed_resources,
        expect=ExpectSuccess(status=_status, json_contains=_modern[4] or {}),
    )(lambda: None)

    endpoint_test(
        method=_method,
        path=_path,
        scenario="forbidden_user_scope",
        input=CaseInput(
            query_params=_resource_forbidden(_input.query_params["path"]),
            headers=_input.headers,
            raw_body=_input.raw_body,
        ),
        seed=_seed_resources_verifier,
        expect=ExpectError(status=403, json_contains={"data": None}),
    )(lambda: None)
