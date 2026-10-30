"""Command lifecycle for the dual-environment structured density journey.

The density case is the dual-environment closure of the structured
collaboration story: the 17-request live journey executes the 15
station-direct structured Group/definition/run obligations (the Gateway does
not deploy the structured Group create route, HTTP 404) plus the two Gateway
catalog observations (the station front door does not serve the catalog
slice, HTTP 401 without the Gateway browser context), and the 43-point
projection runs against the recorded observations.  Owned-local Singlebox
keeps one origin for both families, so the templates leg defaults to the main
profile there (the shared local profile therefore declares both families' route bindings); preprod requires the explicit Gateway catalog profile and strictly split 14/2 binding faces.

A non-PASS journey that already created the Group triggers the reviewed
best-effort delete (expected 200 or 404) before the receipt is written, and
the receipt records the unfinalized disposition honestly.  No credential
value ever enters a receipt: profiles are captured by digest only.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..inventory import executor_digest
from ..local_structured_core_contract import FIXTURE_EFFECT
from ..profile import ConfigError, fingerprint, identifier, load, require_resolved
from ..run_state import RunStateError, write
from ..write_transport import WriteClient
from .live_transport import LiveStructuredDensityTransport
from .profile import (
    DENSITY_STRUCTURED_OPERATIONS,
    DENSITY_TEMPLATES_OPERATIONS,
    validate_density_profile,
    validate_density_templates_profile,
)
from .run import run_structured_density

SCHEMA_VERSION = 1
REPORT_CONTRACT = "dual-structured-density-case-run-v1"
CASE_ID = "DUAL-STRUCTURED-DENSITY-01"
CREDENTIAL_KIND = "human_cookie_and_driver_bot_token"
SUCCESS_RESOURCE_DISPOSITION = FIXTURE_EFFECT
BLOCKED_RESOURCE_DISPOSITION = "UNKNOWN_UNFINALIZED_STRUCTURED_COLLABORATION_GROUP"


def _admit_structured_profile(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ConfigError("structured density profile must be a regular file")
    profile = load(path)
    require_resolved(profile)
    validate_density_profile(profile)
    return profile


def _enforce_structured_binding_set(profile: dict[str, Any], *, shared_catalog_leg: bool) -> None:
    """Bind the reviewed structured operations, keeping each wire face pure.

    The structured leg always declares its 14 reviewed operations.  Owned-local
    Singlebox serves both families from one origin, so the shared single-profile
    default additionally declares the two catalog operations; every separate or
    remote leg keeps the faces strictly split at exactly 14.
    """
    bindings = profile.get("edge_case_bindings", {})
    if not isinstance(bindings, dict):
        raise ConfigError("structured density profile must declare edge_case_bindings")
    declared = set(bindings)
    structured_required = set(DENSITY_STRUCTURED_OPERATIONS)
    if not structured_required <= declared:
        raise ConfigError(
            "structured density profile must bind its 14 reviewed structured operations")
    if shared_catalog_leg:
        if not set(DENSITY_TEMPLATES_OPERATIONS) <= declared:
            raise ConfigError(
                "shared local structured density profile must also bind its two catalog operations")
    elif declared != structured_required:
        raise ConfigError(
            "structured density profile must bind exactly its 14 reviewed structured operations")


def _load_templates_profile(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ConfigError("structured density templates profile must be a regular file")
    profile = load(path)
    require_resolved(profile)
    validate_density_templates_profile(profile)
    bindings = profile.get("edge_case_bindings", {})
    if not isinstance(bindings, dict) or set(bindings) != set(DENSITY_TEMPLATES_OPERATIONS):
        raise ConfigError(
            "structured density templates profile must bind exactly its two reviewed catalog operations")
    return profile


def dual_structured_density_case_run(
    *,
    profile_path: Path,
    state_dir: Path,
    run_id: str,
    allow_write: bool,
    confirm_run_id: str,
    templates_profile_path: Path | None = None,
) -> dict[str, Any]:
    if allow_write is not True or confirm_run_id != run_id:
        raise ConfigError(
            "structured density case requires explicit write permission and exact Run confirmation")
    run_id = identifier(run_id, "structured density run ID")
    separate_templates = templates_profile_path is not None
    profile = _admit_structured_profile(Path(profile_path))
    if not separate_templates and profile["environment"]["tier"] == "preprod":
        raise ConfigError(
            "preprod structured density requires an explicit --templates-profile for the "
            "Gateway catalog origin; the station-direct profile does not serve it")
    _enforce_structured_binding_set(
        profile,
        shared_catalog_leg=(not separate_templates and profile["environment"]["tier"] == "local"))
    templates_profile = (
        _load_templates_profile(Path(templates_profile_path))
        if separate_templates else profile
    )

    if profile["actors"] != templates_profile["actors"] or profile["human"] != templates_profile["human"]:
        raise ConfigError(
            "structured density profiles must declare identical actor and Human identities")

    from .contract import structured_density_plan
    plan = structured_density_plan()
    main_digest = fingerprint(profile)
    templates_digest = fingerprint(templates_profile)
    artifact = executor_digest()

    requested_state_dir = Path(state_dir)
    if (
        not requested_state_dir.is_absolute()
        or requested_state_dir.is_symlink()
        or requested_state_dir.exists()
        or not requested_state_dir.parent.is_dir()
        or requested_state_dir.parent.is_symlink()
    ):
        raise ConfigError("structured density case requires a fresh private state directory")
    state_dir = requested_state_dir.resolve()
    if state_dir.is_symlink() or state_dir.exists():
        raise ConfigError("structured density case requires a fresh private state directory")
    state_dir.mkdir(mode=0o700, parents=False)

    profile_file = state_dir / "structured-density-profile.json"
    templates_profile_file = state_dir / "structured-density-templates-profile.json"
    binding_file = state_dir / "structured-density-binding.json"
    report_file = state_dir / "structured-density-run.json"
    write(profile_file, profile)
    if separate_templates:
        write(templates_profile_file, templates_profile)

    environment = {
        "structured": {
            "tier": profile["environment"]["tier"],
            "id": profile["environment"]["id"],
            "entrypoint_kind": profile["entrypoint"]["kind"],
            "origin": profile["entrypoint"]["origin"],
        },
        "templates": {
            "tier": templates_profile["environment"]["tier"],
            "id": templates_profile["environment"]["id"],
            "entrypoint_kind": templates_profile["entrypoint"]["kind"],
            "origin": templates_profile["entrypoint"]["origin"],
        },
        "shared_origin": not separate_templates,
    }
    case_scope = {
        "parent_obligation_count": plan["parent_obligation_count"],
        "expected_assertion_count": plan["parsed_assertion_count"],
        "new_field_assertion_count": plan["new_field_assertion_count"],
        "structured_operations": sorted(DENSITY_STRUCTURED_OPERATIONS),
        "templates_operations": sorted(DENSITY_TEMPLATES_OPERATIONS),
        "live_journey_steps": 17,
    }
    binding = {
        "schema_version": SCHEMA_VERSION,
        "contract": REPORT_CONTRACT,
        "case_id": CASE_ID,
        "parent_id": plan["parent"],
        "run_id": run_id,
        "profile_digest": main_digest,
        "templates_profile_digest": templates_digest,
        "executor_digest": artifact,
        "credential_kind": CREDENTIAL_KIND,
        "planned_resource_disposition": SUCCESS_RESOURCE_DISPOSITION,
        "case_scope": case_scope,
        "environment": environment,
        "state_dir": str(state_dir),
    }
    write(binding_file, binding)

    transport: LiveStructuredDensityTransport | None = None
    execution: dict[str, Any] | None = None
    try:
        transport = LiveStructuredDensityTransport(profile, templates_profile)
        execution = run_structured_density(profile=profile, transport=transport)
    except (ConfigError, RunStateError, OSError) as error:
        raise ConfigError(
            f"structured density case execution is unavailable: {error}") from None

    cleanup_outcome: dict[str, Any] | None = None
    if execution["status"] != "PASS" and transport is not None:
        group_id = transport.bound_group_id()
        if group_id and not transport.group_delete_executed():
            try:
                _, record = WriteClient(profile).structured_call(
                    "structured.group.delete", group_id=group_id,
                    expected=(200, 404), parse_error=True)
                cleanup_outcome = {
                    "status": "ATTEMPTED",
                    "http_status": record["http_status"],
                    "request_id_echoed": record.get("request_id_echoed"),
                }
            except Exception as error:  # cleanup is best-effort evidence, never a pass
                cleanup_outcome = {"status": "FAILED", "error_type": type(error).__name__}

    status = "PASS" if execution["status"] == "PASS" else "BLOCKED"
    report = {
        "schema_version": SCHEMA_VERSION,
        "contract": REPORT_CONTRACT,
        "case_id": CASE_ID,
        "parent_id": plan["parent"],
        "run_id": run_id,
        "status": status,
        "profile_digest": main_digest,
        "templates_profile_digest": templates_digest,
        "executor_digest": artifact,
        "credential_kind": CREDENTIAL_KIND,
        "environment": environment,
        "case_scope": case_scope,
        "execution": execution,
        "journey": [] if transport is None else transport.journey_log(),
        "cleanup_outcome": cleanup_outcome,
        "resource_disposition": (
            SUCCESS_RESOURCE_DISPOSITION if status == "PASS" else BLOCKED_RESOURCE_DISPOSITION),
        "remote_validation": execution["remote_validation"],
        "grants_release_credit": False,
        "state_dir": str(state_dir),
    }
    write(report_file, report)
    return report
