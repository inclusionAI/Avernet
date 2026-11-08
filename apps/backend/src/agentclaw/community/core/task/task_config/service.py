"""``TaskConfigService`` — typed value encode/validate + match (spec 2026-11-02).

Owns the ``config_type`` semantics the repository does not interpret:

- ``single_string`` — stored as ``"<s>"``; match by ``input == stored``.
- ``bool``          — stored as ``true``/``false``; match by ``input == stored``.
- ``set``           — stored as ``["a", "b", ...]`` (str elements); match by
  ``input in stored`` (input is the single element being tested).

Anti-goal guard (ReviewFocus #5): match validates the input shape BEFORE
comparing, so a stored ``bool`` config matched against the string ``"true"``
raises ``CallbackCorrelationError`` rather than silently equal-comparing.
``isinstance(x, bool)`` is checked before any ``int`` consideration (``bool``
subclasses ``int``), so a stored ``bool`` is never matched by an ``int``
either.

Form/shape mismatches are raised as ``CallbackCorrelationError`` — the task
surface's existing 400-mapped bad-request leaf in ``ENVELOPE_ERRORS`` (``400,
"Bad request"``), per spec §value-encode. A bare ``ValueError`` would re-raise
past ``@envelope_errors`` to the generic 500 handler (wrong status on a normal
client mistake); this reuses the existing mapped leaf rather than introducing
a dedicated exception.

``config_value`` is serialized with ``ensure_ascii=False`` so Chinese config
strings round-trip without escape churn. Unknown config names -> ``False``
(not an error) — a probe must not be able to distinguish "absent" from
"present-but-non-matching" by error behaviour alone.
"""

from __future__ import annotations

import json
from typing import Any

from injector import inject

from agentclaw.community.core.errors import CallbackCorrelationError
from agentclaw.community.core.task.task_config.repository_protocol import (
    TaskConfigRepositoryProtocol,
)
from agentclaw.community.core.task.task_config.task_config_service_protocol import (
    TaskConfigServiceProtocol,
)
from agentclaw.community.log import get_logger

logger = get_logger()

_VALID_CONFIG_TYPES = frozenset({"single_string", "bool", "set"})


def _validate_config_value(config_type: str, config_value: Any) -> None:
    """Raise CallbackCorrelationError if config_value does not match the shape
    config_type names."""
    if config_type == "single_string":
        if not isinstance(config_value, str):
            raise CallbackCorrelationError(
                f"config_type=single_string requires str config_value, got "
                f"{type(config_value).__name__}"
            )
    elif config_type == "bool":
        # bool is a subclass of int — check bool explicitly first.
        if not isinstance(config_value, bool):
            raise CallbackCorrelationError(
                f"config_type=bool requires bool config_value, got "
                f"{type(config_value).__name__}"
            )
    elif config_type == "set":
        if not isinstance(config_value, list) or not all(
            isinstance(e, str) for e in config_value
        ):
            raise CallbackCorrelationError(
                "config_type=set requires list[str] config_value, got "
                f"{type(config_value).__name__}"
            )
    # config_type ∈ the enum is enforced by set_config before reaching here.


def _validate_match_value(config_type: str, value: Any) -> None:
    """Raise CallbackCorrelationError if the match input shape does not match
    config_type.

    single_string / set require a str input (set tests a single element);
    bool requires a real bool — ``"true"`` is a str and must NOT pass.
    """
    if config_type in ("single_string", "set"):
        if not isinstance(value, str):
            raise CallbackCorrelationError(
                f"config_type={config_type} requires str match value, got "
                f"{type(value).__name__}"
            )
    elif config_type == "bool":
        if not isinstance(value, bool):
            raise CallbackCorrelationError(
                f"config_type=bool requires bool match value, got "
                f"{type(value).__name__}"
            )


class TaskConfigService(TaskConfigServiceProtocol):
    """Typed config store + match; env-scoped via the repository."""

    @inject
    def __init__(self, repo: TaskConfigRepositoryProtocol):
        self._repo = repo

    def set_config(
        self,
        *,
        config_name: str,
        config_type: str,
        config_value: Any,
        env: str,
        operator: str | None = None,
    ) -> int:
        if config_type not in _VALID_CONFIG_TYPES:
            raise CallbackCorrelationError(
                f"config_type must be one of {sorted(_VALID_CONFIG_TYPES)}, got {config_type!r}"
            )
        _validate_config_value(config_type, config_value)
        serialized = json.dumps(config_value, ensure_ascii=False)
        config_id = self._repo.upsert(
            config_name=config_name,
            config_type=config_type,
            config_value=serialized,
            env=env,
            operator=operator,
        )
        logger.info(
            "[task_config.set] name=%s env=%s type=%s id=%s operator=%s",
            config_name, env, config_type, config_id, operator,
        )
        return config_id

    def match(self, *, config_name: str, value: Any, env: str) -> bool:
        row = self._repo.get_by_name(env=env, config_name=config_name)
        if row is None:
            # Unknown config name -> matched: False (not an error). A probe
            # cannot tell absent from non-matching by error behaviour.
            return False
        _validate_match_value(row.config_type, value)
        stored = json.loads(row.config_value)
        if row.config_type == "set":
            return value in stored
        # single_string / bool: exact equality after shape validation.
        return value == stored
