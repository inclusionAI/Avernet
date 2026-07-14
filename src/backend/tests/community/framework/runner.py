"""Reflective parametrized pytest runner.

Defines a single test function — ``test_endpoint_injection`` — that
``pytest.mark.parametrize`` expands into one individually-reported
instance per registered :class:`EndpointCase`. The runner owns
invocation and the declared expectation checks; the case author
contributes only data + optional callables.

This is the Java-``@Test``-discovery analog: dropping an annotated
case anywhere in ``tests/endpoints/`` adds one parametrized instance
without touching this module.
"""
from __future__ import annotations

import re
from typing import Any, Mapping

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.community.framework.case import (
    UNSET,
    EndpointCase,
    ExpectError,
    ExpectSuccess,
)
from tests.community.framework.world import World


# Strip FastAPI route convertors (``{name:type}``, e.g. ``{subpath:path}``)
# down to bare ``{name}`` before ``str.format``. ``str.format`` would
# otherwise interpret the convertor as a Python format spec and raise
# ``ValueError: Invalid format specifier``.
_CONVERTOR_RE = re.compile(r"\{([^{}:]+):[^{}]+\}")


def _build_url(path: str, path_params: Mapping[str, Any]) -> str:
    """Substitute ``{name}`` placeholders in ``path`` with values from
    ``path_params``. Missing keys raise ``KeyError`` with the original
    path included so the failure points the author at the right case.

    FastAPI's ``{name:path}`` convertor is normalised to ``{name}`` first
    — the convertor is irrelevant for URL construction.
    """
    normalised = _CONVERTOR_RE.sub(r"{\1}", path)
    try:
        return normalised.format(**path_params)
    except KeyError as exc:
        raise KeyError(
            f"Missing path_param {exc!s} for endpoint path {path!r}"
        ) from exc


def _is_subset(subset: Mapping[str, Any], superset: Any) -> bool:
    """Recursive subset check for ``json_contains``.

    ``subset`` is a dict of expectations; ``superset`` is the actual
    JSON response (or a nested fragment of it). For dicts, every key in
    ``subset`` must exist in ``superset`` and match recursively. For
    lists, every element of ``subset`` must appear (by recursive
    subset-match) as an element of ``superset``. Otherwise: equality.
    """
    if isinstance(subset, dict):
        if not isinstance(superset, dict):
            return False
        for k, v in subset.items():
            if k not in superset:
                return False
            if not _is_subset(v, superset[k]):
                return False
        return True
    if isinstance(subset, list):
        if not isinstance(superset, list):
            return False
        # Each expected item must match SOMETHING in the actual list.
        for expected in subset:
            if not any(_is_subset(expected, actual) for actual in superset):
                return False
        return True
    return subset == superset


def _check_success(response: Any, expect: ExpectSuccess) -> None:
    assert response.status_code == expect.status, (
        f"expected status {expect.status}, got {response.status_code}; "
        f"body={response.text[:500]!r}"
    )
    if expect.json_equals is not UNSET:
        assert response.json() == expect.json_equals, (
            f"json_equals mismatch:\n  expected: {expect.json_equals!r}\n"
            f"  actual:   {response.json()!r}"
        )
    if expect.json_contains:
        body = response.json()
        assert _is_subset(expect.json_contains, body), (
            f"json_contains not satisfied:\n  expected subset: {expect.json_contains!r}\n"
            f"  actual:          {body!r}"
        )


def _check_error(response: Any, expect: ExpectError) -> None:
    assert response.status_code == expect.status, (
        f"expected error status {expect.status}, got {response.status_code}; "
        f"body={response.text[:500]!r}"
    )
    if expect.json_contains:
        body = response.json()
        assert _is_subset(expect.json_contains, body), (
            f"error json_contains not satisfied:\n  expected subset: {expect.json_contains!r}\n"
            f"  actual:          {body!r}"
        )
    # exception_type is best-effort and intentionally permissive here;
    # the first real case that needs richer mapping will tighten the
    # contract. For now, the field is documented but not asserted.


def _prepare_request(case: EndpointCase, world: World) -> tuple[str, dict[str, Any]]:
    """Seed and build the request: returns ``(url, request_kwargs)``.

    Multipart / urlencoded endpoints (``File(...)`` / ``Form(...)``) can't carry a
    JSON body — when the case declares ``form_data`` or ``files``, send those and
    omit ``json``. Otherwise ``json=None`` is the right "no body" signal to httpx.
    """
    if case.seed is not None:
        case.seed(world)

    url = _build_url(case.path, case.input.path_params)

    body_kwargs: dict[str, Any] = {}
    if case.input.files is not None or case.input.form_data is not None:
        if case.input.form_data is not None:
            body_kwargs["data"] = dict(case.input.form_data)
        if case.input.files is not None:
            body_kwargs["files"] = case.input.files
    else:
        body_kwargs["json"] = case.input.json_body

    request_kwargs: dict[str, Any] = {
        "params": dict(case.input.query_params) or None,
        "headers": dict(case.input.headers) or None,
        **body_kwargs,
    }
    return url, request_kwargs


def _check(case: EndpointCase, response: Any, world: World) -> None:
    """Assert the declared expectation, then run the case's extra assertions."""
    if isinstance(case.expect, ExpectSuccess):
        _check_success(response, case.expect)
    elif isinstance(case.expect, ExpectError):
        _check_error(response, case.expect)
    else:  # pragma: no cover — exhaustive union check
        raise TypeError(f"Unknown expectation type: {type(case.expect)!r}")

    for assertion in case.extra_assertions:
        assertion(response, world)


def _run_case(case: EndpointCase, app: FastAPI, world: World) -> None:
    """Drive one (sync) case end-to-end. Pure function over (case, app, world).

    Sequence: seed → build URL → issue request → check expectation →
    run extra_assertions. The runner — not the author — issues the
    request, which is what makes the declared ``(method, path)`` the
    endpoint that was actually exercised.

    A ``seed`` may need to mock a non-DB-backed seam (e.g. a service method
    that reads device files) with ``unittest.mock.patch(...).start()``. Such a
    seed has no teardown hook of its own, so the runner stops every active
    patcher when the case finishes — otherwise a class-level patch would leak
    into later, order-dependent tests. DB-backed setup should still seed real
    rows rather than mock.
    """
    from unittest.mock import patch as _patch

    try:
        _drive_case(case, app, world)
    finally:
        _patch.stopall()


def _drive_case(case: EndpointCase, app: FastAPI, world: World) -> None:
    import asyncio

    url, request_kwargs = _prepare_request(case, world)
    client = TestClient(app)
    response = client.request(case.method, url, **request_kwargs)
    # /process now enqueues a durable stage task instead of running inline; drive
    # it once so the sync case sees the same end state (see _drain_publish_stage_task).
    asyncio.run(_drain_publish_stage_task(world))
    _check(case, response, world)


async def _run_case_async(case: EndpointCase, app: FastAPI, world: World) -> None:
    """Async sibling of :func:`_run_case` for ``drain_background`` cases.

    Drives the endpoint on an in-process async client (``ASGITransport``) so the
    handler's fire-and-forget ``asyncio.create_task`` work runs on the test's loop,
    then awaits it via ``drain_background_tasks`` before checking. Same
    seed/patch-teardown discipline as the sync runner.
    """
    from unittest.mock import patch as _patch

    try:
        await _drive_case_async(case, app, world)
    finally:
        _patch.stopall()


async def _drive_case_async(case: EndpointCase, app: FastAPI, world: World) -> None:
    import httpx
    from httpx import ASGITransport

    from tests.community.framework.endpoint_helpers import drain_background_tasks

    url, request_kwargs = _prepare_request(case, world)
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.request(case.method, url, **request_kwargs)
        await drain_background_tasks()
        await _drain_publish_stage_task(world)
        _check(case, response, world)


async def _drain_publish_stage_task(world: World) -> None:
    """Run the enqueued durable publish *stage* task (verify_flow / online_release)
    once, reproducing what the old inline ``/process`` did synchronously.

    Since the durability refactor, ``/process`` advances the status forward
    synchronously (DRAFT→BUILDING, VALIDATING→ONLINE_PUB) and enqueues a persisted
    task instead of running the stage inline; the test worker is disabled, so
    nothing would run it. One ``run_once()`` claims and runs that single stage task
    (which drives BUILDING→VALIDATE_PUB or runs the online release within ONLINE_PUB
    and enqueues a progress poll — the poll is left un-run, matching the old inline
    end state). Best-effort: silently skips when the task-queue graph isn't wired
    for this app.
    """
    try:
        from agentclaw.community.core.service_bot.services.publish_flow.tasks import (
            PublishTaskLifecycle,
            VERIFY_FLOW_TASK,
        )
        from agentclaw.community.core.task_queue.services.registry import HandlerRegistry
        from agentclaw.community.core.task_queue.services.worker import TaskWorker

        injector = world.injector
        registry = injector.get(HandlerRegistry)
        if registry.get(VERIFY_FLOW_TASK) is None:
            await injector.get(PublishTaskLifecycle).bootstrap()
        await injector.get(TaskWorker).run_once()
    except Exception:  # pragma: no cover - non-publish apps / unwired graphs
        pass


# Note: the parametrized ``test_endpoint_injection`` lives in
# ``tests/endpoints/test_endpoint_runner.py`` rather than here. The
# split is structural — pytest only collects test functions from
# ``test_*.py`` files, and the parametrize must capture
# ``ENDPOINT_CASES`` *after* ``tests/endpoints/conftest.py`` has
# glob-imported every case file. Both constraints are satisfied by
# placing the test there. This module keeps only the runner mechanics.
