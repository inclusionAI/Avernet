"""Endpoint tests for the aicoding data-proxy router.

Targets ``ANY /api/aicoding/data-proxy/{subpath:path}``
(``api/aicoding/data_proxy_router.py``). The catch-all is registered
against six HTTP methods; the coverage gate is happy + error per
verb — 12 cases total.

Both scenarios drive the **real** :class:`DataProxyService` — no
``patch.object``, no mock data. Seeding is delegated to the factories
in :mod:`tests.community.factories.aicoding`:

- **ok_forwarded**: ``route_to_fake_engine`` stands up an in-process
  HTTP server and pins ``AICODING_ENGINE_URL`` at it. The resolver
  tier-1 lands there; ``forward`` makes a real ``httpx`` round-trip;
  the upstream replies ``200 {"forwarded":true,"engine":"fake"}``;
  ``ExpectSuccess.json_contains`` verifies the ``ForwardResult``
  assembly preserved the body.
- **engine_unreachable**: ``route_to_dead_port`` pins
  ``AICODING_ENGINE_URL`` at an OS-grabbed-then-released port.
  ``httpx`` raises ``ConnectError``; the service re-raises
  ``EngineUnreachable``; the global handler maps to HTTP 502.

Together the 12 cases prove the path: decorator → registry →
auto-discovery conftest → parametrized runner → fresh per-test
injector → ``Injected(DataProxyService)`` (bound by
:class:`AICodingModule`) → real ``forward`` → real ``httpx`` → real
upstream (fake engine / dead port) → response → expectation check.

Function bodies are intentionally empty: the framework owns
invocation, the declaration above is the entire case.
"""
from __future__ import annotations

from tests.community.factories.aicoding import route_to_dead_port, route_to_fake_engine
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)


_PATH = "/api/aicoding/data-proxy/{subpath:path}"
_PATH_PARAMS = {"subpath": "api/health"}

_EXPECT_FORWARDED = ExpectSuccess(
    status=200,
    json_contains={"forwarded": True, "engine": "fake"},
)
_EXPECT_UNREACHABLE = ExpectError(
    status=502,
    json_contains={"detail": {"op": "data_proxy_forward"}},
)


# ── GET ──────────────────────────────────────────────────────────────────────


@endpoint_test(
    method="GET",
    path=_PATH,
    scenario="ok_forwarded",
    input=CaseInput(path_params=_PATH_PARAMS),
    seed=route_to_fake_engine,
    expect=_EXPECT_FORWARDED,
)
def get_ok_forwarded():
    """Declarative case — body intentionally empty."""


@endpoint_test(
    method="GET",
    path=_PATH,
    scenario="engine_unreachable",
    input=CaseInput(path_params=_PATH_PARAMS),
    seed=route_to_dead_port,
    expect=_EXPECT_UNREACHABLE,
)
def get_engine_unreachable():
    """Declarative case — body intentionally empty."""


# ── POST ─────────────────────────────────────────────────────────────────────


@endpoint_test(
    method="POST",
    path=_PATH,
    scenario="ok_forwarded",
    input=CaseInput(path_params=_PATH_PARAMS, json_body={"cwd": "/repo"}),
    seed=route_to_fake_engine,
    expect=_EXPECT_FORWARDED,
)
def post_ok_forwarded():
    """Declarative case — body intentionally empty."""


@endpoint_test(
    method="POST",
    path=_PATH,
    scenario="engine_unreachable",
    input=CaseInput(path_params=_PATH_PARAMS, json_body={"cwd": "/repo"}),
    seed=route_to_dead_port,
    expect=_EXPECT_UNREACHABLE,
)
def post_engine_unreachable():
    """Declarative case — body intentionally empty."""


# ── PUT ──────────────────────────────────────────────────────────────────────


@endpoint_test(
    method="PUT",
    path=_PATH,
    scenario="ok_forwarded",
    input=CaseInput(path_params=_PATH_PARAMS, json_body={"x": 1}),
    seed=route_to_fake_engine,
    expect=_EXPECT_FORWARDED,
)
def put_ok_forwarded():
    """Declarative case — body intentionally empty."""


@endpoint_test(
    method="PUT",
    path=_PATH,
    scenario="engine_unreachable",
    input=CaseInput(path_params=_PATH_PARAMS, json_body={"x": 1}),
    seed=route_to_dead_port,
    expect=_EXPECT_UNREACHABLE,
)
def put_engine_unreachable():
    """Declarative case — body intentionally empty."""


# ── PATCH ────────────────────────────────────────────────────────────────────


@endpoint_test(
    method="PATCH",
    path=_PATH,
    scenario="ok_forwarded",
    input=CaseInput(path_params=_PATH_PARAMS, json_body={"x": 1}),
    seed=route_to_fake_engine,
    expect=_EXPECT_FORWARDED,
)
def patch_ok_forwarded():
    """Declarative case — body intentionally empty."""


@endpoint_test(
    method="PATCH",
    path=_PATH,
    scenario="engine_unreachable",
    input=CaseInput(path_params=_PATH_PARAMS, json_body={"x": 1}),
    seed=route_to_dead_port,
    expect=_EXPECT_UNREACHABLE,
)
def patch_engine_unreachable():
    """Declarative case — body intentionally empty."""


# ── DELETE ───────────────────────────────────────────────────────────────────


@endpoint_test(
    method="DELETE",
    path=_PATH,
    scenario="ok_forwarded",
    input=CaseInput(path_params=_PATH_PARAMS),
    seed=route_to_fake_engine,
    expect=_EXPECT_FORWARDED,
)
def delete_ok_forwarded():
    """Declarative case — body intentionally empty."""


@endpoint_test(
    method="DELETE",
    path=_PATH,
    scenario="engine_unreachable",
    input=CaseInput(path_params=_PATH_PARAMS),
    seed=route_to_dead_port,
    expect=_EXPECT_UNREACHABLE,
)
def delete_engine_unreachable():
    """Declarative case — body intentionally empty."""


# ── OPTIONS ──────────────────────────────────────────────────────────────────


@endpoint_test(
    method="OPTIONS",
    path=_PATH,
    scenario="ok_forwarded",
    input=CaseInput(path_params=_PATH_PARAMS),
    seed=route_to_fake_engine,
    expect=_EXPECT_FORWARDED,
)
def options_ok_forwarded():
    """Declarative case — body intentionally empty."""


@endpoint_test(
    method="OPTIONS",
    path=_PATH,
    scenario="engine_unreachable",
    input=CaseInput(path_params=_PATH_PARAMS),
    seed=route_to_dead_port,
    expect=_EXPECT_UNREACHABLE,
)
def options_engine_unreachable():
    """Declarative case — body intentionally empty."""
