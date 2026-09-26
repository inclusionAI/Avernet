# src/backend/tests/community/framework/flow_runner.py
"""FlowRunner — drives a FlowCase end-to-end over TestClient.

Reuses task_runner.py's _build_url/_is_subset. Each step: interpolate path/body
from FlowContext, send request, assert status + (optional) subset expect,
then extract declared fields into the context for later steps.
"""
from __future__ import annotations

import time
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.community.framework.flow import FlowCase, FlowContext, FlowFile, FlowStep
from tests.community.framework.runner import _build_url, _is_subset
from tests.community.framework.world import World


def _dig(payload: Any, dotted: str) -> Any:
    """Follow a dotted path into a JSON payload.

    Dict keys via name (``data.bot_id``); list indices via integer-looking
    segment (``data.sessions.0.id`` ⇒ ``payload["data"]["sessions"][0]["id"]``).
    Out-of-range list index or missing dict key raises KeyError with the
    failing segment named, so the runner's extract failure surfaces precisely.
    """
    cur = payload
    for part in dotted.split("."):
        if isinstance(cur, dict):
            cur = cur[part]
        elif isinstance(cur, list):
            try:
                idx = int(part)
            except ValueError as e:
                raise KeyError(
                    f"cannot dig '{part}' of dotted path {dotted!r}: "
                    f"current value is a list, segment is not an integer"
                ) from e
            try:
                cur = cur[idx]
            except IndexError as e:
                raise KeyError(
                    f"cannot dig '{part}' of dotted path {dotted!r}: "
                    f"index out of range (list length {len(cur)})"
                ) from e
        else:
            raise KeyError(f"cannot dig '{part}' of dotted path {dotted!r}: not a dict at {cur!r}")
    return cur


def _request_kwargs(step: FlowStep, ctx: FlowContext) -> dict[str, Any]:
    kwargs: dict[str, Any] = {}
    if step.query:
        kwargs["params"] = ctx.interpolate_deep(dict(step.query))
    if step.files:
        files = []
        for flow_file in step.files:
            files.append(_multipart_file(flow_file, ctx))
        kwargs["files"] = files
        if step.form is not None:
            kwargs["data"] = ctx.interpolate_deep(dict(step.form))
        return kwargs
    if step.form is not None:
        kwargs["data"] = ctx.interpolate_deep(dict(step.form))
    else:
        kwargs["json"] = ctx.interpolate_deep(dict(step.body)) if step.body is not None else None
    return kwargs


def _multipart_file(flow_file: FlowFile, ctx: FlowContext) -> tuple[str, tuple[str, bytes, str]]:
    filename = ctx.interpolate(flow_file.filename)
    if flow_file.content_bytes is not None:
        content = flow_file.content_bytes
    else:
        content = ctx.interpolate(flow_file.content or "").encode("utf-8")
    return (
        flow_file.field,
        (
            filename,
            content,
            flow_file.content_type,
        ),
    )


def _payload_from_response(resp: Any) -> Any:
    if not resp.content:
        return {}
    try:
        return resp.json()
    except ValueError:
        return {}


def _step_matches(step: FlowStep, resp: Any, payload: Any) -> bool:
    if resp.status_code != step.expect_status:
        return False
    return step.expect is None or _is_subset(step.expect, payload)


def _assert_step_response(
    case: FlowCase,
    step_index: int,
    step: FlowStep,
    resp: Any,
    payload: Any,
) -> None:
    assert resp.status_code == step.expect_status, (
        f"flow '{case.name}' step {step_index} ({step.method} {step.path}): "
        f"status {resp.status_code} != expected {step.expect_status}; body={resp.text[:300]}"
    )

    if step.expect is not None:
        assert _is_subset(step.expect, payload), (
            f"flow '{case.name}' step {step_index} ({step.method} {step.path}): "
            f"expect {step.expect!r} not a subset of response {payload!r}"
        )


def _request_until_step_matches(
    request_once: Any,
    case: FlowCase,
    step_index: int,
    step: FlowStep,
) -> tuple[Any, Any]:
    deadline = time.monotonic() + step.poll_timeout_sec
    while True:
        resp = request_once()
        payload = _payload_from_response(resp)
        if _step_matches(step, resp, payload):
            return resp, payload
        if step.poll_timeout_sec <= 0 or time.monotonic() >= deadline:
            _assert_step_response(case, step_index, step, resp, payload)
            return resp, payload
        time.sleep(max(step.poll_interval_sec, 0))


def run_flow(
    case: FlowCase,
    app: FastAPI,
    world: World,
    initial_context: FlowContext | None = None,
) -> FlowContext:
    """Run every step in order; return the final FlowContext.

    Fails with a precise message naming the failing step index/method/path —
    a mid-flow failure is never disguised as something else.
    """
    ctx = initial_context or FlowContext()
    # Inject a LOCAL identity on every request: x-user-id is the staff_id that
    # LocalAuth (plugins/local/auth.py) reads, equivalent to the frontend
    # DevUserInput. Without it, auth-gated endpoints (e.g. POST /api/skillsets
    # via CollaboratorPermissionInterceptor) 401; this makes the runner an
    # authenticated LOCAL caller, not anonymous.
    client = TestClient(app, headers={"x-user-id": "e2e_user"})

    for i, step in enumerate(case.steps, start=1):
        url = _build_url(ctx.interpolate(step.path), step.path_params)
        # Per-step headers override the base x-user-id (parity with run_flow_live).
        step_headers = dict(step.headers) if step.headers else None
        _, payload = _request_until_step_matches(
            lambda: client.request(
                step.method,
                url,
                headers=step_headers,
                **_request_kwargs(step, ctx),
            ),
            case,
            i,
            step,
        )

        for ctx_key, dotted in step.extract.items():
            ctx[ctx_key] = _dig(payload, dotted)

    return ctx
