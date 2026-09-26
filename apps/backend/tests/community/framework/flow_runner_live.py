# src/backend/tests/community/framework/flow_runner_live.py
"""Live-backend FlowCase executor (路 B / acceptance).

Mirrors flow_runner.run_flow's status/expect/extract semantics so a FlowCase
runs identically in-process (路 A) and against a real backend (路 B). The only
addition is FsAssert: after a step's response validates, physical artifacts on
the host filesystem (under ``fs_root``) are asserted. ``_dig``/``_build_url``/
``_is_subset`` are reused from the in-process runner — one source of truth for
chaining semantics.
"""
from __future__ import annotations

import os
from pathlib import Path

import httpx

from tests.community.framework.flow import FlowCase, FlowContext, FsAssert
from tests.community.framework.flow_runner import _dig, _request_kwargs, _request_until_step_matches
from tests.community.framework.runner import _build_url


DEFAULT_USER_ID = "e2e_user"


def _check_fs_assert(assert_: FsAssert, ctx: FlowContext, fs_root: Path) -> None:
    """Assert one physical artifact. ``path`` is interpolated then resolved
    relative to ``fs_root`` (leading '/' stripped so it joins, not replaces)."""
    rel = ctx.interpolate(assert_.path).lstrip("/")
    path = Path(fs_root) / rel
    if assert_.kind == "exists":
        assert path.exists(), f"FsAssert exists: {path} missing"
    elif assert_.kind == "not_exists":
        assert not path.exists(), f"FsAssert not_exists: {path} exists"
    elif assert_.kind == "is_symlink":
        assert path.is_symlink(), (
            f"FsAssert is_symlink: {path} is not a symlink (exists={path.exists()})"
        )
    elif assert_.kind == "is_file":
        assert path.is_file(), f"FsAssert is_file: {path} is not a file"
    elif assert_.kind == "is_dir":
        assert path.is_dir(), f"FsAssert is_dir: {path} is not a dir"
    elif assert_.kind == "symlink_target":
        assert path.is_symlink(), f"FsAssert symlink_target: {path} is not a symlink"
        target = os.readlink(path)
        expected = ctx.interpolate(assert_.expected_target or "")
        assert target == expected, (
            f"FsAssert symlink_target: {path} -> {target!r}, expected {expected!r}"
        )
    else:  # pragma: no cover — guarded by FsAssert.kind Literal
        raise ValueError(f"Unknown FsAssert kind: {assert_.kind}")


def run_flow_live(
    case: FlowCase,
    base_url: str,
    fs_root: Path | str = "/",
    default_headers: dict[str, str] | None = None,
    initial_context: FlowContext | None = None,
) -> FlowContext:
    """Run a FlowCase against a live backend at ``base_url``.

    fs_root: host filesystem root for FsAssert path resolution. Use "/" in real
      deployments; tests can pass tmp_path or $HOME.
    default_headers: per-flow defaults (e.g. {"x-user-id": "..."}); per-step
      FlowStep.headers override.
    """
    ctx = initial_context or FlowContext()
    base_headers = {"x-user-id": DEFAULT_USER_ID, **(default_headers or {})}
    fs_root = Path(fs_root)

    with httpx.Client(base_url=base_url, headers=base_headers, timeout=60.0) as client:
        for i, step in enumerate(case.steps, start=1):
            step_headers = dict(base_headers)
            if step.headers:
                step_headers.update(step.headers)

            url = _build_url(ctx.interpolate(step.path), step.path_params)
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

            # FsAssert AFTER extract: a path may reference newly-extracted values.
            for fa in step.fs_asserts:
                _check_fs_assert(fa, ctx, fs_root)

    return ctx
