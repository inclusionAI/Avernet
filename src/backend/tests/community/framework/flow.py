# src/backend/tests/community/framework/flow.py
"""Declarative business-flow model (Plan B).

A FlowCase is data: an ordered list of FlowStep. The FlowRunner owns
invocation (so the declared steps are necessarily what runs). FlowContext
carries values extracted from one step's response into later steps via
``{key}`` interpolation.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from tests.community.framework.case import HttpMethod


_PLACEHOLDER = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


class FlowContext:
    """Mutable step-to-step value store with ``{key}`` interpolation."""

    def __init__(self) -> None:
        self._store: dict[str, Any] = {}

    def __setitem__(self, key: str, value: Any) -> None:
        self._store[key] = value

    def __getitem__(self, key: str) -> Any:
        return self._store[key]

    def __contains__(self, key: str) -> bool:
        return key in self._store

    def interpolate(self, template: str) -> str:
        """Replace every ``{key}`` in ``template`` with ``str(self[key])``.
        Raises KeyError if a referenced key is absent (never silent)."""
        def _sub(m: re.Match[str]) -> str:
            key = m.group(1)
            if key not in self._store:
                raise KeyError(f"FlowContext has no key '{key}' for template {template!r}")
            return str(self._store[key])
        return _PLACEHOLDER.sub(_sub, template)

    def interpolate_deep(self, value: Any) -> Any:
        """Recursively interpolate strings inside mappings/lists."""
        if isinstance(value, str):
            return self.interpolate(value)
        if isinstance(value, Mapping):
            return {k: self.interpolate_deep(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self.interpolate_deep(v) for v in value]
        return value


@dataclass(frozen=True)
class FsAssert:
    """Filesystem assertion attached to a FlowStep.

    Executed AFTER the step's HTTP response (status/expect/extract) is
    validated. Different executors treat it differently:
    - In-process (路 A, flow_runner.run_flow): default skip — TestClient has no
      real physical workspace to assert against.
    - Live (路 B, flow_runner_live.run_flow_live): real filesystem assertion
      against the running backend's host filesystem under ``fs_root``.

    ``path`` is resolved relative to the executor's ``fs_root`` and supports
    ``{placeholder}`` interpolation from FlowContext. ``expected_target`` is
    only used for kind="symlink_target" and likewise interpolated.
    """
    kind: Literal[
        "exists", "not_exists", "is_symlink", "is_file", "is_dir", "symlink_target"
    ]
    path: str
    expected_target: str | None = None


@dataclass(frozen=True)
class FlowFile:
    """One multipart file part for a FlowStep.

    ``field`` is the multipart form field name. ``filename`` and text
    ``content`` support ``{placeholder}`` interpolation; binary upload cases can
    pass ``content_bytes`` instead.
    """
    field: str
    filename: str
    content: str | None = None
    content_bytes: bytes | None = None
    content_type: str = "application/octet-stream"


@dataclass(frozen=True)
class FlowStep:
    """One step of a flow: a single endpoint request + how to chain it.

    ``path``/``query``/``body``/``form``/``files`` may contain ``{ctx_key}``
    placeholders interpolated from the FlowContext. ``extract`` maps ctx-key → a dotted JSON path
    into this step's response (e.g. ``"data.bot_id"``). ``expect`` is a
    subset-asserted against the response JSON. ``expect_status`` is the
    required HTTP status. ``fs_asserts`` are physical-artifact checks run by
    the live executor after the response validates (skipped in-process).
    ``headers`` override per-step request headers on top of the executor's
    base headers.
    ``poll_timeout_sec`` lets the runner repeat this same request until both
    HTTP status and ``expect`` match, which models real async state transitions
    such as bot creation becoming ready.
    """
    method: HttpMethod
    path: str
    body: Mapping[str, Any] | None = None
    path_params: Mapping[str, Any] = field(default_factory=dict)
    query: Mapping[str, Any] = field(default_factory=dict)
    form: Mapping[str, Any] | None = None
    files: list[FlowFile] = field(default_factory=list)
    extract: Mapping[str, str] = field(default_factory=dict)
    expect: Mapping[str, Any] | None = None
    expect_status: int = 200
    poll_timeout_sec: float = 0
    poll_interval_sec: float = 1
    fs_asserts: list[FsAssert] = field(default_factory=list)
    headers: Mapping[str, str] | None = None


@dataclass(frozen=True)
class FlowCase:
    """A named business flow: an ordered list of steps + the modules it covers.

    ``live_only`` marks flows that intentionally depend on real singlebox
    infrastructure, such as BaaS/device side effects, and therefore must run
    through the live acceptance executor instead of the in-process TestClient.
    """
    name: str
    steps: list[FlowStep]
    covers: list[str] = field(default_factory=list)
    live_only: bool = False
