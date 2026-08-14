"""Declarative case model for the per-endpoint DI test framework.

A case is **data**, not code: the author declares target method/path,
request input, the expected outcome (success or error), and optional
seed/extra-assertion callables. The framework owns invocation, so the
``(method, path)`` an annotation claims is necessarily the endpoint
exercised — annotations cannot lie about coverage.

This module defines only the data shapes. Registration, fixtures, and
the runner live in sibling modules.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Mapping, Tuple, Union


# Sentinel used to distinguish "not set" from a legitimate ``None`` /
# empty body when interpreting ``ExpectSuccess.json_equals``.
class _Unset:
    _instance: "_Unset | None" = None

    def __new__(cls) -> "_Unset":  # singleton
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:  # pragma: no cover — debug aid only
        return "<UNSET>"

    def __bool__(self) -> bool:
        return False


UNSET: _Unset = _Unset()


HttpMethod = Literal["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]


@dataclass(frozen=True)
class CaseInput:
    """Inputs the framework will use to construct the HTTP request.

    All fields are optional. ``path_params`` values are interpolated into
    ``EndpointCase.path`` placeholders (``/{id}`` → ``/abc``).
    ``query_params`` go on the URL, ``headers`` on the request,
    ``json_body=None`` means no body is sent.

    For multipart / form endpoints (``UploadFile = File(...)`` /
    ``str = Form(...)``), set ``form_data`` (form fields) and/or ``files``
    instead of ``json_body``. ``files`` mirrors httpx's ``files=`` shape:
    a list of ``(field_name, (filename, content_bytes))`` tuples (repeat
    the field name for a ``List[UploadFile]``). When either is set the
    runner sends a multipart/urlencoded body and omits the JSON body.
    """

    path_params: Mapping[str, Any] = field(default_factory=dict)
    query_params: Mapping[str, Any] = field(default_factory=dict)
    headers: Mapping[str, str] = field(default_factory=dict)
    json_body: Any = None
    raw_body: bytes | None = None
    form_data: Mapping[str, Any] | None = None
    files: Any = None


@dataclass(frozen=True)
class ExpectSuccess:
    """Declares the expected successful response.

    Exactly one of ``json_equals`` (exact match) or ``json_contains``
    (subset match against a JSON object) may be set; if both are left at
    their defaults, only the status is asserted.
    """

    status: int = 200
    # Use the UNSET sentinel so callers can legitimately say
    # ``json_equals=None`` to assert the body is JSON ``null``.
    json_equals: Any = UNSET
    json_contains: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ExpectError:
    """Declares the expected error outcome.

    ``status`` is the HTTP status the framework will assert. Note that
    application-level errors often surface as HTTP 200 with an error
    envelope (``{"success": False, "error_code": 404, ...}``); in that
    case set ``status=200`` and use ``json_contains`` to pin the
    envelope shape.

    ``exception_type`` is optional and best-effort: when set, the
    framework also checks that the error envelope looks consistent with
    that domain exception's signature. The precise mapping is left
    intentionally loose until a real case needs it.
    """

    status: int
    json_contains: Mapping[str, Any] = field(default_factory=dict)
    exception_type: type[BaseException] | None = None


Expectation = Union[ExpectSuccess, ExpectError]


# Forward references for callable signatures. ``World`` is defined in
# ``tests/framework/world.py``; ``httpx.Response`` is what FastAPI's
# ``TestClient`` returns. We use ``Any`` here to keep this module free
# of test-runtime imports — the runner will narrow the types in place.
SeedCallable = Callable[[Any], None]
ExtraAssertion = Callable[[Any, Any], None]


@dataclass(frozen=True)
class EndpointCase:
    """One declarative test case targeting one endpoint scenario.

    ``id`` is auto-computed from ``method``, ``path``, and ``scenario``
    and is what pytest displays for the parametrized instance.
    """

    method: HttpMethod
    path: str
    scenario: str
    expect: Expectation
    input: CaseInput = field(default_factory=CaseInput)
    seed: SeedCallable | None = None
    extra_assertions: Tuple[ExtraAssertion, ...] = ()
    # When True the case is driven on an async client and any fire-and-forget
    # background work the handler spawned (``asyncio.create_task``) is awaited
    # before ``expect`` / ``extra_assertions`` run. Use for endpoints that return
    # immediately but finish their work in the background (e.g. the publish
    # ``/process`` build). Default cases stay on the sync ``TestClient`` path.
    drain_background: bool = False
    id: str = field(default="", init=False, compare=False)

    def __post_init__(self) -> None:
        # ``frozen=True`` blocks attribute assignment via ``self.x = ...``,
        # so set via ``object.__setattr__``. This is the documented
        # idiom for computed fields on frozen dataclasses.
        object.__setattr__(self, "id", f"{self.method} {self.path} :: {self.scenario}")
