"""Executable contract for the public BCS Event Catalog."""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from jsonschema import Draft202012Validator
from referencing import Registry, Resource


BCS_ROOT = Path(__file__).resolve().parents[2]
EVENT_ROOT = BCS_ROOT / "api-contracts" / "events" / "v1"
CATALOG_PATH = EVENT_ROOT / "catalog.yaml"
ENVELOPE_SCHEMA_PATH = EVENT_ROOT / "event-envelope.schema.json"
CONTENT_SCHEMA_PATH = EVENT_ROOT / "content-projection.schema.json"
FIXTURE_ROOT = EVENT_ROOT / "fixtures"
SPEC_PATH = (
    BCS_ROOT
    / "docs"
    / "superpowers"
    / "specs"
    / "2026-08-18-bcs-event-subscription-webhook-design.md"
)

EVENT_TYPE_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")
SPEC_EVENT_ROW_PATTERN = re.compile(
    r"^\| `([a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+)` \|",
    re.MULTILINE,
)
METADATA_FORBIDDEN_KEYS = {"text", "json", "preview", "sha256", "fingerprint"}
FULL_FORBIDDEN_KEYS = {
    "internal_prompt",
    "prompt",
    "thinking",
    "tool_arguments",
    "tool_args",
    "token",
    "access_token",
    "share_token",
    "object_handle",
    "internal_url",
}


def _load_yaml(path: Path) -> dict[str, Any]:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(document, dict), f"{path} must contain a mapping"
    return document


def _load_json(path: Path) -> dict[str, Any]:
    import json

    document = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(document, dict), f"{path} must contain an object"
    return document


def _walk(value: Any) -> Iterator[tuple[str | None, Any]]:
    if isinstance(value, dict):
        for key, child in value.items():
            yield key, child
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield None, child
            yield from _walk(child)


@pytest.fixture(scope="module")
def catalog() -> dict[str, Any]:
    return _load_yaml(CATALOG_PATH)


@pytest.fixture(scope="module")
def schema_registry() -> tuple[dict[str, Any], Registry]:
    envelope_schema = _load_json(ENVELOPE_SCHEMA_PATH)
    content_schema = _load_json(CONTENT_SCHEMA_PATH)
    registry = Registry().with_resources(
        [
            (
                envelope_schema["$id"],
                Resource.from_contents(envelope_schema),
            ),
            (
                content_schema["$id"],
                Resource.from_contents(content_schema),
            ),
        ]
    )
    return envelope_schema, registry


def _catalog_events(catalog: dict[str, Any]) -> list[dict[str, Any]]:
    events = catalog.get("events")
    assert isinstance(events, list) and events, "catalog.events must not be empty"
    assert all(isinstance(event, dict) for event in events)
    return events


def _registered_wildcards(catalog: dict[str, Any]) -> set[str]:
    families = catalog.get("families")
    assert isinstance(families, list) and families
    return {family["wildcard"] for family in families}


def _spec_section_16_event_types() -> set[str]:
    spec = SPEC_PATH.read_text(encoding="utf-8")
    section = spec.split("## 16. 首版 Event Catalog", maxsplit=1)[1].split(
        "## 17. 消息持久化改造要求", maxsplit=1
    )[0]
    return set(SPEC_EVENT_ROW_PATTERN.findall(section))


def test_json_schemas_are_valid_draft_2020_12_documents() -> None:
    Draft202012Validator.check_schema(_load_json(ENVELOPE_SCHEMA_PATH))
    Draft202012Validator.check_schema(_load_json(CONTENT_SCHEMA_PATH))


def test_catalog_event_names_are_unique_versioned_and_legal(
    catalog: dict[str, Any],
) -> None:
    assert catalog["spec_version"] == "1.0"
    events = _catalog_events(catalog)
    event_types = [event["event_type"] for event in events]

    assert len(event_types) == len(set(event_types))
    assert all(EVENT_TYPE_PATTERN.fullmatch(event_type) for event_type in event_types)
    assert all(event["schema_version"] == "1.0" for event in events)
    assert all(event["data_schema"].startswith("#/$defs/") for event in events)


def test_catalog_exactly_matches_the_spec_section_16_event_inventory(
    catalog: dict[str, Any],
) -> None:
    actual = {event["event_type"] for event in _catalog_events(catalog)}
    assert actual == _spec_section_16_event_types()


def test_every_catalog_event_has_a_discriminated_data_schema(
    catalog: dict[str, Any],
    schema_registry: tuple[dict[str, Any], Registry],
) -> None:
    envelope_schema, _ = schema_registry
    definitions = envelope_schema["$defs"]
    events = _catalog_events(catalog)

    for event in events:
        definition_name = event["data_schema"].removeprefix("#/$defs/")
        assert definition_name in definitions
        for field in event.get("content_fields", []):
            assert definitions[definition_name]["properties"][field] == {
                "$ref": "#/$defs/ContentProjection"
            }

    discriminated_types = {
        definitions[variant["$ref"].removeprefix("#/$defs/")]["properties"][
            "event_type"
        ]["const"]
        for variant in envelope_schema["oneOf"]
    }
    assert discriminated_types == {event["event_type"] for event in events}
    assert "judge" not in CATALOG_PATH.read_text(encoding="utf-8").lower()


def test_family_wildcards_are_registered_namespaces_with_events(
    catalog: dict[str, Any],
) -> None:
    events = _catalog_events(catalog)
    event_types = {event["event_type"] for event in events}
    wildcards = _registered_wildcards(catalog)

    assert {"group.*", "session.*", "task.*", "state_machine.*", "message.*"} <= wildcards
    for wildcard in wildcards:
        assert EVENT_TYPE_PATTERN.fullmatch(wildcard.removesuffix(".*") + ".event")
        prefix = wildcard.removesuffix("*")
        assert any(event_type.startswith(prefix) for event_type in event_types)

    for event in events:
        assert event["family"] in wildcards
        assert event["event_type"].startswith(event["family"].removesuffix("*"))


def test_representative_fixtures_validate_against_envelope_and_data_schemas(
    catalog: dict[str, Any],
    schema_registry: tuple[dict[str, Any], Registry],
) -> None:
    envelope_schema, registry = schema_registry
    envelope_validator = Draft202012Validator(envelope_schema, registry=registry)
    events = {
        event["event_type"]: event for event in _catalog_events(catalog)
    }
    fixture_paths = sorted(FIXTURE_ROOT.glob("*.json"))
    stream_prefixes = {
        "group": "group:",
        "session": "session:",
        "task": "task:",
        "state_machine_run": "state-machine-run:",
    }

    assert fixture_paths, "at least one representative Event fixture is required"
    represented_families: set[str] = set()
    for path in fixture_paths:
        fixture = _load_json(path)
        event = events[fixture["event_type"]]
        represented_families.add(event["family"].split(".", maxsplit=1)[0] + ".*")

        assert fixture["subject"]["type"] == event["subject_type"]
        assert set(event["required_scope"]) <= set(fixture["scope"])
        assert fixture["stream"]["key"].startswith(stream_prefixes[event["stream"]])
        envelope_validator.validate(fixture)
        data_validator = Draft202012Validator(
            {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "$ref": envelope_schema["$id"] + event["data_schema"],
            },
            registry=registry,
        )
        data_validator.validate(fixture["data"])

    assert represented_families == {
        "group.*",
        "session.*",
        "task.*",
        "state_machine.*",
        "message.*",
    }


def test_node_started_predecessors_are_additive_and_unique(
    schema_registry: tuple[dict[str, Any], Registry],
) -> None:
    envelope_schema, registry = schema_registry
    validator = Draft202012Validator(
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$ref": envelope_schema["$id"] + "#/$defs/StateMachineNodeStartedData",
        },
        registry=registry,
    )
    legacy_data = {
        "run_id": "run-1",
        "node_id": "join",
        "attempt": 0,
        "started_at": "2026-08-21T00:00:00.000Z",
    }

    validator.validate(legacy_data)
    validator.validate(
        {
            **legacy_data,
            "predecessor_node_ids": ["branch-b", "branch-c"],
        }
    )
    assert list(
        validator.iter_errors(
            {
                **legacy_data,
                "predecessor_node_ids": ["branch-b", "branch-b"],
            }
        )
    )


def test_metadata_only_fixtures_never_expose_content_or_derived_identifiers() -> None:
    fixture_paths = sorted(FIXTURE_ROOT.glob("*.metadata_only.json"))
    assert fixture_paths

    for path in fixture_paths:
        fixture = _load_json(path)
        exposed = {
            key for key, _ in _walk(fixture["data"]) if key in METADATA_FORBIDDEN_KEYS
        }
        assert not exposed, f"{path.name} exposes metadata-only keys: {sorted(exposed)}"


def test_full_fixtures_exclude_internal_reasoning_credentials_and_urls() -> None:
    fixture_paths = sorted(FIXTURE_ROOT.glob("*.full.json"))
    assert fixture_paths

    for path in fixture_paths:
        fixture = _load_json(path)
        exposed = {key for key, _ in _walk(fixture) if key in FULL_FORBIDDEN_KEYS}
        urls = [
            value
            for _, value in _walk(fixture)
            if isinstance(value, str) and value.startswith(("http://", "https://"))
        ]
        assert not exposed, f"{path.name} exposes forbidden keys: {sorted(exposed)}"
        assert not urls, f"{path.name} exposes URLs: {urls}"
