"""Named preprod admission-gap ledger for the Bot-authority density slice.

The owned-local density replay closes all 42 shell obligations offline. The
preprod leg cannot execute them, and this ledger names every distinct
admission gap with its missing materials and missing grants instead of
inferring a pass. Nothing here is a product defect claim: the owned-local
LIVE leg already closed 42/42 under the dual authority envelope, the
upstream lenient-sync scope note is disclosed as file-header context, and
the preprod blockers are admission posture facts derived from the density
profile route bindings, the pilot route binding review table, and the
landed dual authority preprod semantics.
"""
from __future__ import annotations

from typing import Any

from ..run_state import RunStateError
from .contract import (CALL_GROUP_COUNT, MINE_USER_ID, PARSED_OBLIGATION_COUNT,
                       TARGET_USER_ID, parsed_obligations)

PREPROD_COLUMN = "REMOTE_ADMISSION_GATED_READ_ONLY"
PREPROD_GAP_IDS = (
    "AUTHORITY_PREPROD_ADMISSION_ROUTE_BINDING_UNREVIEWED",
    "AUTHORITY_PREPROD_ADMISSION_CREDENTIAL_REFS_UNRESOLVED",
    "AUTHORITY_PREPROD_ADMISSION_MUTATING_COLUMN_FROZEN",
    "AUTHORITY_PREPROD_ADMISSION_TRUSTED_REGISTER_PLANE_ABSENT",
    "AUTHORITY_PREPROD_ADMISSION_PRINCIPAL_SIGNING_MATERIAL",
    "AUTHORITY_PREPROD_ADMISSION_IDENTITY_PRESEED",
    "AUTHORITY_PREPROD_ADMISSION_CLI_BUSINESS_VERBS_ABSENT",
)

# Operations whose obligations name the transfer-target human (BCS_E2E_AUTHORITY_STAFF_B)
# or the mine-ensured human (BCS_E2E_AUTHORITY_STAFF_MINE): preprod identity preseed.
IDENTITY_PRESEED_OPERATIONS = frozenset((
    "managers.add.target", "managers.page.populated", "managers.remove.target",
    "managers.remove.absent", "managers.restore.target", "public.mine.read",
    "managers.add.mine", "managers.remove.mine", "transfer.initiate",
    "transfer.refresh", "transfer.target.read", "transfer.accept",
    "ownership.target.read", "managers.ledger.target.read",
    "transfers.inbox.received", "transfers.inbox.sent",
))

PRODUCT_DEFECTS: dict[str, dict[str, Any]] = {}

DOCUMENTED_UPSTREAM_SCOPE_NOTES = (
    {"lines": [29, 36], "summary": "lenient-sync trusted-App legs are covered by shared "
     "conformance suites and the Rust e2e chain; this story drives no trusted-app scope"},
    {"lines": [38, 41], "summary": "every business call carries a Gateway Principal JWT; "
     "the public collaboration story probe mints it"},
)


def _roster_ids() -> tuple[str, ...]:
    return tuple(row.obligation_id for row in parsed_obligations())


def _ids_for_operations(operations: frozenset[str]) -> tuple[str, ...]:
    return tuple(row.obligation_id for row in parsed_obligations()
                 if row.operation in operations)


def _gap(id_tail: str, scope: str, detail: str, missing_materials: list[str],
         missing_authorizations: list[str], derivation: list[str],
         resolution: str, blocks: tuple[str, ...]) -> dict[str, Any]:
    return {
        "id": "AUTHORITY_PREPROD_ADMISSION_" + id_tail,
        "status": "NOT_RUN",
        "scope": scope,
        "detail": detail,
        "missing_materials": missing_materials,
        "missing_authorizations": missing_authorizations,
        "derivation_basis": derivation,
        "resolution": resolution,
        "blocks": list(blocks),
    }


def preprod_admission_gaps() -> tuple[dict[str, Any], ...]:
    """Return the ordered named-gap ledger for the frozen preprod column."""
    all_ids = _roster_ids()
    identity_ids = _ids_for_operations(IDENTITY_PRESEED_OPERATIONS)
    return (
        _gap(
            "ROUTE_BINDING_UNREVIEWED",
            "all-obligations",
            "The preprod density profile can only drive the 20 authority business "
            "operations through reviewed route bindings, but the pilot route binding "
            "review table admits group/session public-v1 operations only; no authority "
            "operation owns a reviewed binding row. The landed r8 preprod leaf recorded "
            "both credential-free route probes failing at transport (URLError, no HTTP "
            "status observed) instead of the frozen healthy-JSON health surface and the "
            "unauthenticated registration-mint rejection.",
            ["reviewed preprod route binding rows (method, path, api_face, auth, response, "
             "traceparent) for all 20 authority business operations across the "
             "internal-and-public-v1 faces",
             "one live station route observation proving the declared bcs-direct origin "
             "serves the /api/v1/collaboration and /openapi/v1/collaboration authority paths"],
            ["route binding review admission for the authority operation set"],
            ["coverage-map row 18: preprod r8 verdict BLOCKED with "
             "PREPROD_AUTHORITY_ROUTE_PROBE_MISMATCH",
             "r8 leaf authority-dual-run.json route_probe_observations: transport_error "
             "URLError, observed_status null on health and register-token-unauthenticated",
             "bcs_e2e.route_bindings ROUTES reviews the initial pilot operations only "
             "(group create/read/update/delete and session lifecycle); validate_route_bindings "
             "requires exactly that pilot set",
             "density preprod profile declares bcs-direct with api_face "
             "internal-and-public-v1, so the declared face set exceeds the reviewed table"],
            "Mainland reviews and lands authority route bindings or re-points the density "
            "preprod profile at an origin whose reviewed bindings cover the 20 operations; "
            "until then the preprod leg stays NOT_RUN.",
            all_ids,
        ),
        _gap(
            "CREDENTIAL_REFS_UNRESOLVED",
            "all-obligations",
            "The preprod duo admission model binds Authorization to the driver Bot "
            "credential reference BCS_E2E_DRIVER_TOKEN and Cookie to the human session "
            "reference BCS_E2E_HUMAN_COOKIE by name only. The 2026-10-30 trio probe and "
            "the r8/MWP2 evidence trail recorded both references expired at the station "
            "ACE login gate. This slice discloses the references and never mints, refreshes, "
            "or retries expired authentication.",
            ["freshly admitted driver Bot credential behind BCS_E2E_DRIVER_TOKEN",
             "freshly admitted human session behind BCS_E2E_HUMAN_COOKIE"],
            ["preprod driver Bot authorization grant",
             "preprod human session grant for the story owner"],
            ["density and dual authority preprod profiles bind auth.header_env by "
             "environment reference only",
             "coverage-map row 18 r8 detail records the credential admission blocks",
             "family dual leg context: member bot gek online while the driver token and "
             "human cookie expired (ACE LOGIN) on 2026-10-30"],
            "The release owner re-admits fresh duo credentials out-of-band through the "
            "operator seam; no credential value transits this package.",
            all_ids,
        ),
        _gap(
            "MUTATING_COLUMN_FROZEN",
            "all-obligations",
            "The frozen preprod execution column for this story is "
            "REMOTE_ADMISSION_GATED_READ_ONLY: it authorizes credential-free route probes "
            "plus two read-only authenticated GETs, never the journey. All 42 obligations "
            "ride fixture-authoring, manager add/remove, transfer initiate/refresh/accept "
            "and their dependent reads, so the column itself excludes every one of them.",
            [],
            ["unfrozen preprod execution column for the mutating authority journey, or an "
             "explicitly authorized read-only projection of the story"],
            ["authority dual contract ENVIRONMENT preprod column "
             "REMOTE_ADMISSION_GATED_READ_ONLY",
             "family dual preprod leg reason class: mutating journey outside the frozen "
             "read-only column",
             "coverage-map row 18 dual status BLOCKED(r8) with the read-only probe posture "
             "maintained"],
            "The column owner unfreezes the journey or defines a sanctioned read-only "
            "projection; the density slice keeps the fail-close posture.",
            all_ids,
        ),
        _gap(
            "TRUSTED_REGISTER_PLANE_ABSENT",
            "all-obligations",
            "The story provisions two disposable fixture Bots and seeds the owner and "
            "target human actor rows through the trusted registration plane (station token "
            "mint plus commit under explicit mock identity headers). That plane is an "
            "owned-local bootstrap surface: mock identity headers are admitted on the "
            "registration plane only inside the strict local configuration, and no "
            "preprod grant admits mock identity or trusted-app registration.",
            ["two pre-disposed preprod fixture Bots with distinct ids",
             "live authority-registry actor rows for the owner human and the target human "
             "(BCS_E2E_AUTHORITY_STAFF_B)"],
            ["trusted registration plane access on preprod (absent by design)"],
            ["upstream fixture registration calls at story lines 121-135",
             "authority dual contract registration_plane: mint /register/token, commit "
             "/register, mock headers forbidden on business paths, bots_committed 2",
             "owned-local strict admission configuration allow_mock_headers only on the "
             "local registration plane"],
            "Preprod fixtures must arrive through a real provisioning grant outside this "
            "slice; none is claimed and the roster stays BLOCKED_INVENTORIED.",
            all_ids,
        ),
        _gap(
            "PRINCIPAL_SIGNING_MATERIAL",
            "all-obligations",
            "Every business assertion rides a response obtained with a freshly minted "
            "HS256 Gateway Principal JWT carried via the gateway principal assertion header, signed with "
            "the AVERNET_SECRET_PRINCIPAL_SIGNING_KEY_VALUE material inside a 60-second "
            "window. The preprod signing material is not admitted to this offline slice, "
            "which references the environment name only.",
            ["admitted preprod principal signing material for the Gateway Principal mint"],
            ["per-request Gateway Principal minting grant against the preprod origin"],
            ["upstream principal probe wiring and signing-key environment reference",
             "authority dual contract principal block: HS256, kid bare, issuer gateway, "
             "audience bcs, tenant bcs-e2e, fresh mint per business request, 60s expiry"],
            "Mainline admits signing material through the operator secret seam; the density "
            "package never handles a value.",
            all_ids,
        ),
        _gap(
            "IDENTITY_PRESEED",
            "identity-named-obligations",
            "33 of the 42 obligations name the transfer-target human (frozen local fixture "
            "002 behind BCS_E2E_AUTHORITY_STAFF_B) or the mine-ensured human (frozen local "
            "fixture 137 behind BCS_E2E_AUTHORITY_STAFF_MINE). On preprod the authority "
            "Human registry is the live identity table: the mine leg is ensured only by an "
            "authenticated public bots/mine read for a real ACE-admitted staff number, and "
            "the target leg needs a real preseeded human actor row. Mock staff ids are "
            "local-tier fixtures and cannot be replayed remotely.",
            ["real preprod staff identity for the transfer target",
             "real preprod staff identity for the mine-ensured human",
             "live human actor rows in the preprod authority registry"],
            ["identity preseed grant for the two preprod staff numbers",
             "ACE-admitted sessions bound to those staff numbers"],
            ["upstream defaults BCS_E2E_AUTHORITY_STAFF_B=002 and mine default 137 are local "
             "fixtures, mirrored frozen in the authority dual contract",
             "the public bots/mine read is the only ensurer of the mine-ensured human "
             "(upstream spec section 3.1, strict-edit acceptance)",
             "33 obligations carry TARGET_USER_ID or MINE_USER_ID expected values or "
             "target/mine actor bindings"],
            "The release owner supplies preprod staff numbers and ACE-admitted sessions via "
            "the profile authority environment references; the local constants stay local.",
            identity_ids,
        ),
        _gap(
            "CLI_BUSINESS_VERBS_ABSENT",
            "entry-plane",
            "The frozen bcs-cli owns exactly the product readiness gate and exposes no "
            "authority business verbs, so even with transport admitted the preprod leg has "
            "no CLI fallback for the 42 business obligations; the HTTP child design is the "
            "only journey driver. This is an entry-plane inventory fact, not an "
            "obligation-level blocker.",
            [],
            ["none claimed by this slice; CLI product gap logged for the CLI owner"],
            ["authority dual contract CLI separation note: bcs owns the server leg, bcs-cli "
             "owns only the child readiness gate",
             "cli_runtime_binary authority_business_verbs is frozen to none-frozen"],
            "Not a blocker for the HTTP child design; inventoried so mainline never mistakes "
            "the readiness gate for business coverage.",
            (),
        ),
    )


def gap_attribution() -> dict[str, tuple[str, ...]]:
    """Attribute the named gaps to each of the 42 obligations by rule."""
    identity = frozenset(_ids_for_operations(IDENTITY_PRESEED_OPERATIONS))
    attribution: dict[str, tuple[str, ...]] = {}
    for row in parsed_obligations():
        gaps = [PREPROD_GAP_IDS[0], PREPROD_GAP_IDS[1], PREPROD_GAP_IDS[2],
                PREPROD_GAP_IDS[3], PREPROD_GAP_IDS[4]]
        if row.obligation_id in identity:
            gaps.append(PREPROD_GAP_IDS[5])
        attribution[row.obligation_id] = tuple(gaps)
    return attribution


def admission_diagnosis_basis() -> dict[str, Any]:
    """Static derivation inputs for the preprod admission diagnosis."""
    return {
        "frozen_column": PREPROD_COLUMN,
        "route_review": {
            "reviewed_pilot_operations": 12,
            "authority_operations_unreviewed": CALL_GROUP_COUNT,
            "pilot_table": "bcs_e2e.route_bindings ROUTES (group/session public-v1 only)",
            "consequence": "validate_route_bindings rejects any non-pilot operation set, "
                           "so the 20 authority operations cannot even be declared reviewed",
        },
        "credential_refs": ["BCS_E2E_DRIVER_TOKEN", "BCS_E2E_HUMAN_COOKIE"],
        "staff_refs": ["BCS_E2E_AUTHORITY_STAFF_B", "BCS_E2E_AUTHORITY_STAFF_MINE"],
        "signing_key_env_ref": "AVERNET_SECRET_PRINCIPAL_SIGNING_KEY_VALUE",
        "frozen_local_fixtures": {"target_user_id": TARGET_USER_ID, "mine_user_id": MINE_USER_ID},
        "r8_evidence": {
            "classification": "PREPROD_AUTHORITY_ROUTE_PROBE_MISMATCH",
            "observed_statuses": {"health": None, "register-token-unauthenticated": None},
            "transport_errors": "URLError",
            "authenticated_probe_routes": ["ownership-read-driver-bearer",
                                           "public-mine-read-human-cookie"],
            "leaf": "~/.codex/bcs-e2e/preprod-live-evidence/20261031-regression-r8-preprod/"
                    "authority/authority-r8-state/authority-dual-run.json",
            "family_disposition": "AUTHORITY_PREPROD_ADMISSION_BLOCKED",
        },
        "trio_context": ("2026-10-30 trio probe: member bot gek online; driver bot token and "
                         "human cookie both expired at the station ACE gate (ACE LOGIN)"),
        "coverage_map_row": 18,
        "coverage_map_queue": "rank 2, gap_qty 42, feasibility 3, score 126",
    }


def defect_gap_ledger() -> dict[str, Any]:
    gaps = {gap["id"]: gap for gap in preprod_admission_gaps()}
    return {
        "product_defects": dict(PRODUCT_DEFECTS),
        "documented_upstream_scope_notes": list(DOCUMENTED_UPSTREAM_SCOPE_NOTES),
        "preprod_column": PREPROD_COLUMN,
        "gaps": gaps,
        "gap_ids": list(PREPROD_GAP_IDS),
        "parsed_obligation_count": PARSED_OBLIGATION_COUNT,
        "call_group_count": CALL_GROUP_COUNT,
        "identity_preseed_obligations": len(_ids_for_operations(IDENTITY_PRESEED_OPERATIONS)),
    }


def assert_honesty_ledger() -> None:
    gaps = preprod_admission_gaps()
    if [gap["id"] for gap in gaps] != list(PREPROD_GAP_IDS):
        raise RunStateError("authority density preprod gap ledger changed")
    all_ids = set(_roster_ids())
    if len(all_ids) != PARSED_OBLIGATION_COUNT:
        raise RunStateError("authority density roster changed during gap attribution")
    for gap in gaps:
        if gap["status"] != "NOT_RUN":
            raise RunStateError("every named preprod gap must stay NOT_RUN: " + gap["id"])
        if set(gap["blocks"]) - all_ids:
            raise RunStateError("a named preprod gap blocks an unknown obligation: " + gap["id"])
    by_id = {gap["id"]: gap for gap in gaps}
    for gap_id in PREPROD_GAP_IDS[:5]:
        if set(by_id[gap_id]["blocks"]) != all_ids:
            raise RunStateError("universal admission gap lost an obligation: " + gap_id)
    if set(by_id[PREPROD_GAP_IDS[5]]["blocks"]) != set(_ids_for_operations(IDENTITY_PRESEED_OPERATIONS)):
        raise RunStateError("identity preseed attribution changed")
    if by_id[PREPROD_GAP_IDS[6]]["blocks"]:
        raise RunStateError("entry-plane CLI gap must block no obligation")
    attribution = gap_attribution()
    if set(attribution) != all_ids:
        raise RunStateError("gap attribution must cover every obligation")
    for obligation_id, gap_ids in attribution.items():
        if list(gap_ids[:5]) != list(PREPROD_GAP_IDS[:5]):
            raise RunStateError("universal gaps missing from attribution: " + obligation_id)
        if len(gap_ids) not in (5, 6):
            raise RunStateError("attribution size drifted: " + obligation_id)
    if PRODUCT_DEFECTS:
        raise RunStateError("authority density invents no product defect; local LIVE closed 42/42")
