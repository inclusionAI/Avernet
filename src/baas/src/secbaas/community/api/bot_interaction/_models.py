"""Result dataclasses returned by the interaction service contract."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

InteractionKind = Literal["ask_user", "exec", "mode_switch"]


@dataclass(frozen=True, slots=True)
class InteractionResolution:
    """Transport-independent answer queued for the Engine websocket owner."""

    decision: str
    kind: InteractionKind | None = None
    answer: str | None = None
    message: str | None = None
    values: dict[str, str] | None = None
    answers: dict[str, str] | None = None
    selected_options: tuple[tuple[str, ...], ...] | None = None

    def __post_init__(self) -> None:
        _require_non_empty_string(self.decision, "decision")
        if self.kind not in {None, "ask_user", "exec", "mode_switch"}:
            raise ValueError("interaction resolution kind is invalid")
        _validate_optional_string(self.answer, "answer")
        _validate_optional_string(self.message, "message")
        _validate_optional_string_map(self.values, "values")
        _validate_optional_string_map(self.answers, "answers")
        if self.selected_options is not None:
            if not self.selected_options:
                raise ValueError(
                    "interaction resolution selectedOptions cannot be empty"
                )
            for options in self.selected_options:
                if not options:
                    raise ValueError(
                        "interaction resolution selectedOptions entries cannot be empty"
                    )
                for option in options:
                    _require_string(option, "selectedOptions value")

    def to_dict(self) -> dict[str, object]:
        """Encode the durable JSON shape, omitting absent optional fields."""
        result: dict[str, object] = {"decision": self.decision}
        if self.kind is not None:
            result["kind"] = self.kind
        if self.answer is not None:
            result["answer"] = self.answer
        if self.message is not None:
            result["message"] = self.message
        if self.values is not None:
            result["values"] = dict(self.values)
        if self.answers is not None:
            result["answers"] = dict(self.answers)
        if self.selected_options is not None:
            result["selectedOptions"] = [
                list(options) for options in self.selected_options
            ]
        return result

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> InteractionResolution:
        """Decode a persisted resolution without accepting malformed rows."""
        kind = value.get("kind")
        if kind is not None and not isinstance(kind, str):
            raise ValueError("interaction resolution kind must be a string")
        return cls(
            decision=_required_string_field(value, "decision"),
            kind=kind,  # type: ignore[arg-type]
            answer=_optional_string_field(value, "answer"),
            message=_optional_string_field(value, "message"),
            values=_optional_string_map_field(value, "values"),
            answers=_optional_string_map_field(value, "answers"),
            selected_options=_optional_selected_options(value),
        )


@dataclass(frozen=True, slots=True)
class InteractionDispatch:
    """Validated command claimed by the websocket owner."""

    session_key: str
    interaction_id: str
    resolution: InteractionResolution


@dataclass(frozen=True, slots=True)
class InteractionRequestedResult:
    """Public identity and persistence outcome for an Engine request event."""

    baas_interaction_id: str
    created: bool


@dataclass(frozen=True, slots=True)
class InteractionResolveResult:
    """Public interaction identity accepted from an uplink adapter."""

    interaction_id: str


@dataclass(frozen=True, slots=True)
class InteractionResolvedResult:
    """Public identity and transition outcome for an Engine terminal event."""

    baas_interaction_id: str
    applied: bool


def _required_string_field(value: dict[str, object], key: str) -> str:
    item = value.get(key)
    _require_non_empty_string(item, key)
    return item


def _optional_string_field(value: dict[str, object], key: str) -> str | None:
    if key not in value:
        return None
    item = value[key]
    _require_non_empty_string(item, key)
    return item


def _optional_string_map_field(
    value: dict[str, object], key: str
) -> dict[str, str] | None:
    if key not in value:
        return None
    item = value[key]
    if not isinstance(item, dict):
        raise ValueError(f"interaction resolution {key} must be an object")
    result: dict[str, str] = {}
    for map_key, map_value in item.items():
        _require_non_empty_string(map_key, f"{key} key")
        _require_string(map_value, f"{key} value")
        result[map_key] = map_value
    if not result:
        raise ValueError(f"interaction resolution {key} cannot be empty")
    return result


def _optional_selected_options(
    value: dict[str, object],
) -> tuple[tuple[str, ...], ...] | None:
    if "selectedOptions" not in value:
        return None
    item = value["selectedOptions"]
    if not isinstance(item, list):
        raise ValueError("interaction resolution selectedOptions must be an array")
    groups: list[tuple[str, ...]] = []
    for group in item:
        if not isinstance(group, list):
            raise ValueError(
                "interaction resolution selectedOptions entries must be arrays"
            )
        groups.append(tuple(group))
    return tuple(groups)


def _validate_optional_string(value: str | None, name: str) -> None:
    if value is not None:
        _require_non_empty_string(value, name)


def _validate_optional_string_map(value: dict[str, str] | None, name: str) -> None:
    if value is None:
        return
    if not value:
        raise ValueError(f"interaction resolution {name} cannot be empty")
    for map_key, map_value in value.items():
        _require_non_empty_string(map_key, f"{name} key")
        _require_string(map_value, f"{name} value")


def _require_string(value: object, name: str) -> None:
    if not isinstance(value, str):
        raise ValueError(f"interaction resolution {name} must be a string")


def _require_non_empty_string(value: object, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"interaction resolution {name} must be a non-empty string")
