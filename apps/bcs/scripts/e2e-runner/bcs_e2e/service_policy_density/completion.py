"""Wave-D marker-completion obligations for the retired service policy story.

This module is the additive Wave-D extension of the landed
``SERVICE_POLICY_DENSITY_R1_PROJECTED_20261031`` first wave.  The first wave
projected a 40-point density superset (eight flat parents plus thirty-two
field expansions) over the frozen ``service_policy_story.py`` journey; the
coverage-map ledger for row #19 ``story_retired_service_policy`` still carried
the coarse marker-caliber gap ``parsed 23 checks - 8 flat wrapper obligations
= 15`` with queue basis ``delta=15x2; 形态差核对+P4 关联``.  This layer closes
exactly that ledger gap with fifteen marker-completion leaves
``SERVICE-POLICY.F33`` through ``SERVICE-POLICY.F47`` (family ids continue the
R1 field numbering), one per remaining marker-charged assertion unit, grouped
by the five ledger-named gap groups:

* seven LEGACY leaves - the second-iteration legacy creation/read-back status
  checks and the four policy-absence envelope checks (absent
  create/read);
* one RETIRED leaf - the retired PATCH settings unit (``F40``), the contested
  site: the frozen station form answers 410 ``service_policy_retired`` while
  the poolab sim roster observed 200; ``F40`` re-charges the contested unit
  with ``basis=documented-exception`` and registers the divergence as the
  named product/form-difference item
  ``SERVICE-POLICY-RETIRED-SETTINGS-410-200-SIM-STATION-FORM-DIFFERENCE``
  (coverage-map material gap M11 member ``service-policy 410 形态差``);
* three INVOCATION leaves - the second creation and both stored-input reads
  (invocation not blocked);
* one GUARD leaf - the post-refusal input persistence read (running conflict);
* three CONVERSION leaves - completion, conversion refusal and the final
  read (chat completion guard).

Calibers are disclosed and never merged: the marker caliber is 23 (seven
uncontested flat thematic charges a/c/e/j/n/p plus the contested i, the
bootstrap plane units b (disclosed derivation probe, never obligatable) and
d (station row bound to the flat registration plane), fourteen remaining
obligatable units, and the single contested recharge through ``F40``); the
R1 density superset caliber stays 40; the full family density roster is 55
rows.  The ledger formula closes with
``residual_count_gap = 23 - 8 - 15 = 0``.

The 410-vs-200 divergence is registered, never re-judged: the flat
``SERVICE-POLICY.retired-settings-gone`` wrapper and the R1 ``F13``/``F14``
rows keep their frozen 410 station expectation byte-identical, the sim
observation of 200 is preserved as documented-exception evidence, the local
replay charges only the station form (the owned-local Singlebox answered 410
on 2026-10-30 and the r9-transportfix-duo preprod product-http leg journaled
410 on 2026-10-31), and the preprod completion leg stays an honest
``NOT_RUN`` named gap.  The contested unit's closure is gated on the P4
provider live-admission material gate plus the M11 product-defect
disposition.  Nothing here executes a transport or resolves a credential.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..local_service_policy_contract import REQUIREMENTS
from ..run_state import RunStateError
from .contract import DENSITY_OPERATIONS, DUAL_SERVICE_POLICY_DENSITY_COMMAND
from .obligations import (
    CONVERSION,
    FIELD_OBLIGATION_COUNT,
    GUARD,
    INVOCATION,
    LEGACY,
    MARKER_CHARGED_CHECKS,
    PARSED_OBLIGATION_COUNT,
    PARENT,
    RETIRED,
    SOURCE_FILE,
    SOURCE_PATH,
    TEARDOWN,
)

SCHEMA_VERSION = 1
LAYER = "MARKER_COMPLETION_WAVE_D"
CONTRACT = "service-policy-density-completion-v1"
COMPLETION_WAVE_TOKEN = "SERVICE_POLICY_DENSITY_15_OBLIGATIONS_20261031"
R1_WAVE_TOKEN = "SERVICE_POLICY_DENSITY_R1_PROJECTED_20261031"

COMPLETION_LEAF_COUNT = 15
LEDGER_GAP = 15
RESIDUAL_COUNT_GAP = 0
# Upstream story marker caliber and the flat wrapper denominator.
FLAT_WRAPPER_OBLIGATIONS = 8
FLAT_THEMATIC_MARKER_CHARGES = 6
BOOTSTRAP_PLANE_UNITS = 2
CONTESTED_SITE_RECHARGES = 1

STORY_SH_FILE = "apps/bcs/scripts/e2e-test/service_policy_story.sh"
STORY_SH_PATH = SOURCE_PATH.with_name("service_policy_story.sh")
# SHA-256 freeze of both upstream sources at the reviewed worktree state
# (branch codex/e2e-dev-fresh-31bbfdc, HEAD f0be65cf8; the story files were
# last touched by the 0873001b6 retirement commit and their behavior
# literals were frozen by the flat binding at
# 31bbfdc933ebf5e9361a273ea16b668326092b48).  Any upstream edit fails
# closed here instead of silently re-basing the completion roster.
STORY_PY_SHA256 = "66aa329bf8e3ca31e563f244514098203cfd1bfc68fbefa7ed865aa7f1c722f9"
STORY_SH_SHA256 = "be1c8bbc2f7d37d69cdab2f77f77fffcd78ca61be720b4a0341fdb72e6a145c5"

BASIS_FROZEN_ANCHOR = "frozen-anchor"
BASIS_DOCUMENTED_EXCEPTION = "documented-exception"

GAP_GROUP_LEGACY = "legacy-policy-absent-create-read"
GAP_GROUP_RETIRED = "retired-settings-gone-410"
GAP_GROUP_INVOCATION = "invocation-not-blocked"
GAP_GROUP_RUNNING = "running-conflict"
GAP_GROUP_CONVERSION = "chat-completion-guard"
GAP_GROUPS = (
    GAP_GROUP_LEGACY, GAP_GROUP_RETIRED, GAP_GROUP_INVOCATION,
    GAP_GROUP_RUNNING, GAP_GROUP_CONVERSION,
)

STATUS_KIND = "status"
ENVELOPE_KIND = "envelope"
HTTP_STATUS = "http_status"
HTTP_STATUS_ENUM = "http_status_enum"
POLICY_ENVELOPE_ABSENT = "policy_envelope_absent"
ENVELOPE_FIELDS = ("service_spec", "retired_service_spec")
ABSENCE_DEF_SPAN = (51, 54)
ABSENCE_DEF_QUOTE = (
    '    def assert_policy_absent(self, group: dict) -> None:\n'
    '        assert "service_spec" not in group, "Retired policy leaked into Group response"\n'
    '        assert "retired_service_spec" not in group, "Compatibility shim leaked into response"\n'
    "        self.checks += 1")

# The named product/form-difference item (the wave core).  The station form
# is frozen from the reviewed Rust retirement gate; the sim form is the
# recorded poolab sim observation.  Registered, never re-judged.
FORM_DIFFERENCE_ITEM_ID = (
    "SERVICE-POLICY-RETIRED-SETTINGS-410-200-SIM-STATION-FORM-DIFFERENCE")
FORM_DIFFERENCE_DEFECT_ID = "SERVICE-POLICY-410-FORM-DIFFERENCE"
COVERAGE_MAP_MATERIAL_GAP = "M11"
STATION_GATE_SOURCE = (
    "apps/bcs/crates/adapters/http/bcs-http/src/routes/groups/handlers.rs"
    ":632-637")
P4_GATE_ID = "SERVICE_POLICY_DENSITY_P4_MATERIAL_GATE"

FORM_DIFFERENCE_NAMED_ITEM: dict[str, Any] = {
    "item_id": FORM_DIFFERENCE_ITEM_ID,
    "classification": "product/form-difference",
    "status": "PRODUCT_FORM_DIFFERENCE_ON_FILE",
    "parent": PARENT,
    "contested_site": {
        "source_file": SOURCE_FILE,
        "line_start": 81,
        "line_end": 83,
        "quote": (
            '        retired = self.request("PATCH", f"/groups/{group}/settings",\n'
            '                               {"service_spec": {"enabled": True}}, status=410)\n'
            '        assert retired["error"] == "service_policy_retired", retired'),
        "operation": "service_policy.settings_patch_retired",
        "method": "PATCH",
        "path": "/groups/{group_id}/settings",
    },
    "station_form": {
        "expected_status": 410,
        "error": "service_policy_retired",
        "basis": (
            "router.rs-reviewed station form: " + STATION_GATE_SOURCE +
            " patch_group_settings answers StatusCode::GONE unconditionally "
            'with error service_policy_retired; the R1 projection and the '
            "flat SERVICE-POLICY.retired-settings-gone wrapper freeze this "
            "form"),
        "observed_410_evidence": [
            ("owned-local Singlebox live 2026-10-30: "
             "~/.codex/bcs-e2e/20261030-dual-service-policy-local-live/state/"
             "owned-service-policy/console.log L119 and logs/e2e-access.jsonl "
             'L20 journal PATCH /groups/{id}/settings status=410'),
            ("preprod product-http r9-transportfix-duo 2026-10-31: retired-"
             "settings-gone step data status=410 error=service_policy_retired "
             "(~/.codex/bcs-e2e/preprod-live-evidence/20261031-regression-"
             "r9-transportfix-duo-preprod/service-policy/); the coverage map "
             "keeps r8 as the authoritative BLOCKED verdict, so this is "
             "disclosed as an observation, never a re-judged verdict"),
        ],
    },
    "sim_form": {
        "observed_status": 200,
        "basis": (
            "poolab roster 2026-10-29 third-shot sim deployment recorded the "
            "PATCH settings expectation 410 against an observed 200; frozen "
            "program record: coverage-map row 19 dual note (期望 410 实得 "
            "200 的 sim/产品形态差待核对), the 2026-10-31 audit export "
            "/tmp/assertion-gap-matrix.md row 19 (sim 形态与 410 语义待对 "
            "齐), and the R1 frozen poolab_form_difference_note"),
    },
    "registered_leaf": "SERVICE-POLICY.F40",
    "basis": BASIS_DOCUMENTED_EXCEPTION,
    "disposition": (
        "registered-not-rejudged: the frozen 410 station expectation and the "
        "flat/R1 rows stay byte-identical; the sim observation of 200 is "
        "preserved as exception evidence; the owned-local replay charges only "
        "the station form and its PASS is a station-form replay truth that "
        "grants zero sim or preprod credit; no row is silently re-judged"),
    "defect_registry": {
        "defect_id": FORM_DIFFERENCE_DEFECT_ID,
        "coverage_map_material_gap": COVERAGE_MAP_MATERIAL_GAP,
        "material_gap_status": "产品在案（不阻塞本地登记，阻塞预发实弹）",
    },
    "credit_posture": (
        "owned-local tier may PASS the station form; preprod completion leg "
        "stays NOT_RUN; remote_validation=NOT_RUN; grants_release_credit="
        "false"),
}

MATERIAL_GATE: dict[str, Any] = {
    "gate_id": P4_GATE_ID,
    "gate_family": (
        "P4 provider live-admission patch family (coverage-map patch waves "
        "P0-P6; landed L4 commit 3c6d6f374 honest read-only preprod "
        "admission surface)"),
    "association": (
        "coverage-map row 19 queue basis ranks this story as delta=15x2 with "
        "形态差核对+P4 关联; the contested 410 unit re-charged by leaf "
        "SERVICE-POLICY.F40 can only be dispositioned by an authorized "
        "preprod station observation after the P4 admission posture is in "
        "place; the M11 on-record product defect (service-policy 410 形态差) "
        "blocks preprod live fire but never blocks local registration"),
    "requires": [
        "P4 provider live-admission landing observed (coverage-map L4 "
        "3c6d6f374)",
        "Human Cookie credential window refresh under explicit user "
        "authorization (environment variable name BCS_E2E_HUMAN_COOKIE only; "
        "values never touched)",
        "owned dedicated driver Bot bearer reference (environment variable "
        "name BCS_E2E_DRIVER_TOKEN only; values never touched)",
        "authorized dual-service-policy density preprod rerun recording the "
        "PATCH /groups/{group_id}/settings observation on the preprod "
        "station",
        "product disposition of the sim/station divergence through the M11 "
        "defect registry",
    ],
    "blocks": (
        "preprod live-fire closure of the contested 410 unit (leaf F40) and "
        "the completion layer's preprod leg until gate " + P4_GATE_ID +
        " is observed; owned-local registration and replay are unaffected"),
    "disclosed_not_executed": True,
}

COMPLETION_GAP_ID = "SERVICE_POLICY_DENSITY_COMPLETION_PREPROD_TRANSPORT_GAP"
GAP_LEDGER: dict[str, dict[str, Any]] = {
    "GAP-SP-D-01": {
        "gap_id": "GAP-SP-D-01",
        "scope": "preprod execution of the 15 marker-completion leaves",
        "kind": "EXECUTION_GAP_NOT_RUN",
        "status": "NOT_RUN_RECEIPT_ONLY",
        "basis": (
            "no preprod network call is authorized for this wave; the "
            "offline projector fail-closes the preprod leg to "
            "BLOCKED_INVENTORIED with every completion leaf NOT_RUN, "
            "extending the family transport posture of "
            "SERVICE_POLICY_DENSITY_PREPROD_TRANSPORT_GAP"),
        "closure_requirement": (
            "authorized preprod station observation through the mainline "
            "serial run seam after the P4 material gate; the offline "
            "projector never opens a transport"),
        "blocks": "all 15 completion leaves on the preprod tier",
        "parsed_unit_claimed": True,
    },
    "GAP-SP-D-02": {
        "gap_id": "GAP-SP-D-02",
        "scope": "retired settings PATCH station 410 vs sim observed 200",
        "kind": "PRODUCT_FORM_DIFFERENCE_ON_FILE",
        "status": "REGISTERED_NOT_REJUDGED",
        "basis": (
            "named item " + FORM_DIFFERENCE_ITEM_ID + " (coverage-map "
            "material gap " + COVERAGE_MAP_MATERIAL_GAP + " member); leaf "
            "SERVICE-POLICY.F40 re-charges the contested unit with basis "
            "documented-exception; the station form is frozen from "
            + STATION_GATE_SOURCE + " and the sim form is preserved from "
            "the poolab roster record"),
        "closure_requirement": (
            "authorized preprod station observation under the P4 material "
            "gate (" + P4_GATE_ID + ") plus the M11 product-defect "
            "disposition; only an authorized wave may disposition the "
            "exception"),
        "blocks": "verdict finality of the contested 410 unit; local station-form replay unaffected",
        "parsed_unit_claimed": True,
    },
}

# (short_id, parent, family, gap_group, label, method, path, caller,
#  operation, line_start, line_end, source_quote, assert_line, assert_quote,
#  request_line, request_quote, envelope_def, field, comparator, expected,
#  comparator_text, expectation, basis, documented_exception)
_COMPLETION_SITES: tuple[tuple, ...] = (
    ("F33", "SERVICE-POLICY.legacy-encoding-ignored", LEGACY,
     GAP_GROUP_LEGACY,
     "object-encoding created Group envelope hides both retired policy fields",
     "POST", "/groups", "driver", "service_policy.group_create_object",
     77, 77, '            self.assert_policy_absent(created)',
     77, '            self.assert_policy_absent(created)',
     70, '            created = self.request("POST", "/groups", {',
     True, None, POLICY_ENVELOPE_ABSENT, None,
     "the created response carries neither service_spec nor retired_service_spec",
     "first legacy loop iteration (object encoding): the marker absence "
     "check on the created Group charges both retired-policy fields absent; "
     "the wrapped creation status unit rides the flat legacy parent and "
     "R1 density rows F01-F03",
     BASIS_FROZEN_ANCHOR, None),
    ("F34", "SERVICE-POLICY.legacy-encoding-ignored", LEGACY,
     GAP_GROUP_LEGACY,
     "object-encoding Group read-back answers 200",
     "GET", "/groups/{group_id}", "driver", "service_policy.group_read_object",
     78, 78,
     '            self.assert_policy_absent(self.request("GET", f"/groups/{group}"))',
     78,
     '            self.assert_policy_absent(self.request("GET", f"/groups/{group}"))',
     78,
     '            self.assert_policy_absent(self.request("GET", f"/groups/{group}"))',
     False, None, HTTP_STATUS, 200, "http_status 200",
     "first legacy loop iteration (object encoding): the read-back request "
     "answers 200; the compound input-envelope fields ride R1 density rows "
     "F04-F06",
     BASIS_FROZEN_ANCHOR, None),
    ("F35", "SERVICE-POLICY.legacy-encoding-ignored", LEGACY,
     GAP_GROUP_LEGACY,
     "object-encoding read-back Group envelope hides both retired policy fields",
     "GET", "/groups/{group_id}", "driver", "service_policy.group_read_object",
     78, 78,
     '            self.assert_policy_absent(self.request("GET", f"/groups/{group}"))',
     78,
     '            self.assert_policy_absent(self.request("GET", f"/groups/{group}"))',
     78,
     '            self.assert_policy_absent(self.request("GET", f"/groups/{group}"))',
     True, None, POLICY_ENVELOPE_ABSENT, None,
     "the read-back response carries neither service_spec nor "
     "retired_service_spec",
     "first legacy loop iteration (object encoding): the marker absence "
     "check on the read-back Group charges both retired-policy fields "
     "absent; the wrapped read-back status unit is leaf F34",
     BASIS_FROZEN_ANCHOR, None),
    ("F36", "SERVICE-POLICY.legacy-encoding-ignored", LEGACY,
     GAP_GROUP_LEGACY,
     "null-encoding (compatibility shim) Group creation answers 200/201",
     "POST", "/groups", "driver", "service_policy.group_create_null",
     70, 74,
     '            created = self.request("POST", "/groups", {\n'
     '                "driver_bot": self.bot, "label": "Retired policy compatibility",\n'
     '                "participants": [{"bot_uuid": self.bot, "role": "driver"}],\n'
     '                "service_spec": policy,\n'
     '            })',
     70, '            created = self.request("POST", "/groups", {',
     70, '            created = self.request("POST", "/groups", {',
     False, None, HTTP_STATUS_ENUM, (200, 201), "200 or 201",
     "second legacy loop iteration (null compatibility shim): the created "
     "Group is accepted with 200 or 201 while persisting no policy",
     BASIS_FROZEN_ANCHOR, None),
    ("F37", "SERVICE-POLICY.legacy-encoding-ignored", LEGACY,
     GAP_GROUP_LEGACY,
     "null-encoding created Group envelope hides both retired policy fields",
     "POST", "/groups", "driver", "service_policy.group_create_null",
     77, 77, '            self.assert_policy_absent(created)',
     77, '            self.assert_policy_absent(created)',
     70, '            created = self.request("POST", "/groups", {',
     True, None, POLICY_ENVELOPE_ABSENT, None,
     "the created response carries neither service_spec nor retired_service_spec",
     "second legacy loop iteration (null compatibility shim): the marker "
     "absence check on the created Group charges both retired-policy "
     "fields absent; the wrapped creation status unit is leaf F36",
     BASIS_FROZEN_ANCHOR, None),
    ("F38", "SERVICE-POLICY.legacy-encoding-ignored", LEGACY,
     GAP_GROUP_LEGACY,
     "null-encoding Group read-back answers 200",
     "GET", "/groups/{group_id}", "driver", "service_policy.group_read_null",
     78, 78,
     '            self.assert_policy_absent(self.request("GET", f"/groups/{group}"))',
     78,
     '            self.assert_policy_absent(self.request("GET", f"/groups/{group}"))',
     78,
     '            self.assert_policy_absent(self.request("GET", f"/groups/{group}"))',
     False, None, HTTP_STATUS, 200, "http_status 200",
     "second legacy loop iteration (null compatibility shim): the read-back "
     "request answers 200; the compound envelope fields ride R1 density "
     "rows F10-F12",
     BASIS_FROZEN_ANCHOR, None),
    ("F39", "SERVICE-POLICY.legacy-encoding-ignored", LEGACY,
     GAP_GROUP_LEGACY,
     "null-encoding read-back Group envelope hides both retired policy fields",
     "GET", "/groups/{group_id}", "driver", "service_policy.group_read_null",
     78, 78,
     '            self.assert_policy_absent(self.request("GET", f"/groups/{group}"))',
     78,
     '            self.assert_policy_absent(self.request("GET", f"/groups/{group}"))',
     78,
     '            self.assert_policy_absent(self.request("GET", f"/groups/{group}"))',
     True, None, POLICY_ENVELOPE_ABSENT, None,
     "the read-back response carries neither service_spec nor "
     "retired_service_spec",
     "second legacy loop iteration (null compatibility shim): the marker "
     "absence check on the read-back Group charges both retired-policy "
     "fields absent; the wrapped read-back status unit is leaf F38",
     BASIS_FROZEN_ANCHOR, None),
    ("F40", "SERVICE-POLICY.retired-settings-gone", RETIRED,
     GAP_GROUP_RETIRED,
     "retired settings PATCH answers 410 with the sim-observed 200 form registered as documented exception",
     "PATCH", "/groups/{group_id}/settings", "driver",
     "service_policy.settings_patch_retired",
     81, 83,
     '        retired = self.request("PATCH", f"/groups/{group}/settings",\n'
     '                               {"service_spec": {"enabled": True}}, status=410)\n'
     '        assert retired["error"] == "service_policy_retired", retired',
     82,
     '                               {"service_spec": {"enabled": True}}, status=410)',
     81,
     '        retired = self.request("PATCH", f"/groups/{group}/settings",',
     False, "error", HTTP_STATUS, 410, "http_status 410",
     "the contested retired settings unit: the frozen station form answers "
     "410 service_policy_retired (the compound error-code assert rides R1 "
     "density row F14); the poolab sim deployment observed 200, registered "
     "as the named product/form-difference item without re-judging any "
     "frozen row",
     BASIS_DOCUMENTED_EXCEPTION, FORM_DIFFERENCE_NAMED_ITEM),
    ("F41", "SERVICE-POLICY.invocation-lifecycle", INVOCATION,
     GAP_GROUP_INVOCATION,
     "second service_invocation creation answers 202",
     "POST", "/services/{group_id}/sessions", "driver",
     "service_policy.invocation_create_second",
     86, 88,
     '            invoked = self.request("POST", f"/services/{group}/sessions", {\n'
     '                "session_title": f"Legacy policy ignored {index}", "input": {"run": index},\n'
     '            }, status=202)',
     88, '            }, status=202)',
     86,
     '            invoked = self.request("POST", f"/services/{group}/sessions", {',
     False, None, HTTP_STATUS, 202, "http_status 202",
     "second legacy-policy Group still admits service invocation creation "
     "with 202 (invocation not blocked); the compound session_kind/status "
     "asserts ride R1 density rows F19-F21",
     BASIS_FROZEN_ANCHOR, None),
    ("F42", "SERVICE-POLICY.invocation-lifecycle", INVOCATION,
     GAP_GROUP_INVOCATION,
     "first invocation stored-input read answers 200",
     "GET", "/services/{group_id}/sessions/{session_id}", "driver",
     "service_policy.invocation_read_first",
     93, 93,
     '            stored = self.request("GET", f"/services/{group}/sessions/{sid}")',
     93,
     '            stored = self.request("GET", f"/services/{group}/sessions/{sid}")',
     93,
     '            stored = self.request("GET", f"/services/{group}/sessions/{sid}")',
     False, None, HTTP_STATUS, 200, "http_status 200",
     "the first invocation stored-input read answers 200; the persisted "
     "input {\"run\": 0} assert rides R1 density row F18",
     BASIS_FROZEN_ANCHOR, None),
    ("F43", "SERVICE-POLICY.invocation-lifecycle", INVOCATION,
     GAP_GROUP_INVOCATION,
     "second invocation stored-input read answers 200",
     "GET", "/services/{group_id}/sessions/{session_id}", "driver",
     "service_policy.invocation_read_second",
     93, 93,
     '            stored = self.request("GET", f"/services/{group}/sessions/{sid}")',
     93,
     '            stored = self.request("GET", f"/services/{group}/sessions/{sid}")',
     93,
     '            stored = self.request("GET", f"/services/{group}/sessions/{sid}")',
     False, None, HTTP_STATUS, 200, "http_status 200",
     "the second invocation stored-input read answers 200; the persisted "
     "input {\"run\": 1} assert and the distinct-session-id assert ride R1 "
     "density rows F22-F23",
     BASIS_FROZEN_ANCHOR, None),
    ("F44", "SERVICE-POLICY.running-reactivation-denied", GUARD,
     GAP_GROUP_RUNNING,
     "post-refusal input persistence read answers 200",
     "GET", "/services/{group_id}/sessions/{session_id}", "driver",
     "service_policy.reactivation_read",
     102, 102,
     '        assert self.request("GET", f"/services/{group}/sessions/{sid}")["input"] == {"run": 0}',
     102,
     '        assert self.request("GET", f"/services/{group}/sessions/{sid}")["input"] == {"run": 0}',
     102,
     '        assert self.request("GET", f"/services/{group}/sessions/{sid}")["input"] == {"run": 0}',
     False, None, HTTP_STATUS, 200, "http_status 200",
     "the running-conflict aftermath read answers 200; the preserved input "
     "{\"run\": 0} compound assert rides R1 density row F26",
     BASIS_FROZEN_ANCHOR, None),
    ("F45", "SERVICE-POLICY.chat-session-guard", CONVERSION,
     GAP_GROUP_CONVERSION,
     "chat Session completion answers 200",
     "POST", "/sessions/{session_id}/complete", "driver",
     "service_policy.chat_complete",
     109, 109,
     '        completed = self.request("POST", f"/sessions/{sid}/complete", {"output": {"ok": True}})',
     109,
     '        completed = self.request("POST", f"/sessions/{sid}/complete", {"output": {"ok": True}})',
     109,
     '        completed = self.request("POST", f"/sessions/{sid}/complete", {"output": {"ok": True}})',
     False, None, HTTP_STATUS, 200, "http_status 200",
     "the guard journey completes the chat Session with 200; the compound "
     "completed-status assert rides R1 density row F28",
     BASIS_FROZEN_ANCHOR, None),
    ("F46", "SERVICE-POLICY.chat-session-guard", CONVERSION,
     GAP_GROUP_CONVERSION,
     "completed chat Session conversion refusal answers 400",
     "POST", "/services/{group_id}/sessions", "driver",
     "service_policy.conversion_denied",
     111, 112,
     '        invalid = self.request("POST", f"/services/{group}/sessions",\n'
     '                               {"session_id": sid}, status=400)',
     112, '                               {"session_id": sid}, status=400)',
     111,
     '        invalid = self.request("POST", f"/services/{group}/sessions",',
     False, None, HTTP_STATUS, 400, "http_status 400",
     "converting the completed chat Session into a service invocation is "
     "refused with 400; the compound not-service_invocation error assert "
     "rides R1 density row F30",
     BASIS_FROZEN_ANCHOR, None),
    ("F47", "SERVICE-POLICY.chat-session-guard", CONVERSION,
     GAP_GROUP_CONVERSION,
     "final chat Session read-back answers 200",
     "GET", "/sessions/{session_id}", "driver", "service_policy.chat_final_read",
     114, 114, '        stored = self.request("GET", f"/sessions/{sid}")',
     114, '        stored = self.request("GET", f"/sessions/{sid}")',
     114, '        stored = self.request("GET", f"/sessions/{sid}")',
     False, None, HTTP_STATUS, 200, "http_status 200",
     "the final chat Session read-back answers 200; the compound "
     "completed-status and intact-output assert rides R1 density rows "
     "F31-F32",
     BASIS_FROZEN_ANCHOR, None),
)


@dataclass(frozen=True)
class CompletionObligation:
    """One Wave-D marker-completion leaf with an exact source anchor."""

    obligation_id: str
    layer: str
    level: str
    parent_obligation: str
    family: str
    gap_group: str
    label: str
    kind: str
    method: str
    path: str
    caller: str
    operation: str
    line_start: int
    line_end: int
    source_quote: str
    assert_line: int
    assert_quote: str
    request_line: int
    request_quote: str
    envelope_def: bool
    field: str | None
    comparator: str
    expected: Any
    comparator_text: str
    expectation: str
    basis: str
    documented_exception: str | None
    density: bool
    preprod_mode: str
    located: bool = True

    def as_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "obligation_id": self.obligation_id,
            "layer": self.layer,
            "level": self.level,
            "parent_obligation": self.parent_obligation,
            "family": self.family,
            "gap_group": self.gap_group,
            "label": self.label,
            "kind": self.kind,
            "method": self.method,
            "path": self.path,
            "caller": self.caller,
            "operation": self.operation,
            "source": {
                "file": SOURCE_FILE,
                "line_start": self.line_start,
                "line_end": self.line_end,
                "quote": self.source_quote,
                "assert_line": self.assert_line,
                "assert_quote": self.assert_quote,
                "request_line": self.request_line,
                "request_quote": self.request_quote,
                "envelope_def": self.envelope_def,
                "located": self.located,
            },
            "field": self.field,
            "comparator": self.comparator,
            "expected": self.expected if not isinstance(self.expected, tuple)
            else list(self.expected),
            "comparator_text": self.comparator_text,
            "expectation": self.expectation,
            "basis": self.basis,
            "density": self.density,
            "preprod_mode": self.preprod_mode,
        }
        if self.documented_exception is not None:
            value["documented_exception"] = self.documented_exception
        return value


def _sha256_file(path: Path) -> str:
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise RunStateError(
            f"service policy density completion source is unavailable: {error}") from None
    return hashlib.sha256(raw).hexdigest()


def _verify_source_freeze(lines: list[str]) -> None:
    if _sha256_file(SOURCE_PATH) != STORY_PY_SHA256:
        raise RunStateError(
            "service policy density completion source sha256 drifted: "
            "service_policy_story.py")
    if _sha256_file(STORY_SH_PATH) != STORY_SH_SHA256:
        raise RunStateError(
            "service policy density completion source sha256 drifted: "
            "service_policy_story.sh")
    if not lines[55].startswith("    def run(self) -> None:"):
        raise RunStateError("service policy density completion story run() header changed")
    if "HTTP/response checks passed" not in lines[115]:
        raise RunStateError("service policy density completion story marker line changed")
    if ABSENCE_DEF_QUOTE != "\n".join(lines[ABSENCE_DEF_SPAN[0] - 1:ABSENCE_DEF_SPAN[1]]):
        raise RunStateError(
            "service policy density completion absence-def span drifted")
    shell_text = STORY_SH_PATH.read_text(encoding="utf-8")
    if "E2E_TESTS_STORIES+=(story_retired_service_policy)" not in shell_text:
        raise RunStateError("service policy density completion shell registration changed")


def build_service_policy_density_completion_obligations() -> tuple[CompletionObligation, ...]:
    """Build and anchor-validate the fifteen Wave-D completion leaves.

    The roster freezes the fifteen marker-completion leaves F33-F47 with
    verbatim multi-line source spans; the SHA-256 freeze of both upstream
    story files fails closed on any drift before any anchor is evaluated.
    """
    _verify_source_freeze(SOURCE_PATH.read_text(encoding="utf-8").splitlines())
    lines = SOURCE_PATH.read_text(encoding="utf-8").splitlines()
    known_parents = set(REQUIREMENTS)
    rows: list[CompletionObligation] = []
    for index, (short_id, parent, family, gap_group, label, method, path,
                caller, operation, line_start, line_end, source_quote,
                assert_line, assert_quote, request_line, request_quote,
                envelope_def, field, comparator, expected, comparator_text,
                expectation, basis, documented_exception) in enumerate(
            _COMPLETION_SITES, start=33):
        obligation_id = f"SERVICE-POLICY.F{index:02d}"
        if not obligation_id.endswith(short_id):
            raise RunStateError(
                f"service policy density completion id sequence drifted: {short_id}")
        if parent not in known_parents:
            raise RunStateError(
                f"service policy density completion leaf {obligation_id} "
                f"references unknown parent {parent}")
        if operation not in DENSITY_OPERATIONS:
            raise RunStateError(
                f"service policy density completion leaf {obligation_id} "
                f"binds unreviewed operation {operation}")
        if method not in {"GET", "POST", "PATCH"}:
            raise RunStateError(
                f"service policy density completion leaf {obligation_id} "
                f"carries an unreviewed method {method}")
        if not (1 <= line_start <= line_end <= len(lines)):
            raise RunStateError(
                f"service policy density completion leaf {obligation_id} "
                "span is out of source bounds")
        if "\n".join(lines[line_start - 1:line_end]) != source_quote:
            raise RunStateError(
                f"service policy density completion source span drifted: "
                f"{obligation_id} (service_policy_story.py:{line_start}-"
                f"{line_end})")
        if assert_quote not in lines[assert_line - 1]:
            raise RunStateError(
                f"service policy density completion assert anchor drifted: "
                f"{obligation_id} (service_policy_story.py:{assert_line})")
        if request_quote not in lines[request_line - 1]:
            raise RunStateError(
                f"service policy density completion request anchor drifted: "
                f"{obligation_id} (service_policy_story.py:{request_line})")
        if envelope_def and ABSENCE_DEF_QUOTE != "\n".join(
                lines[ABSENCE_DEF_SPAN[0] - 1:ABSENCE_DEF_SPAN[1]]):
            raise RunStateError(
                f"service policy density completion envelope def drifted: "
                f"{obligation_id}")
        if basis not in {BASIS_FROZEN_ANCHOR, BASIS_DOCUMENTED_EXCEPTION}:
            raise RunStateError(
                f"service policy density completion leaf {obligation_id} "
                f"carries an unreviewed basis {basis}")
        if (basis == BASIS_DOCUMENTED_EXCEPTION) != (documented_exception is not None):
            raise RunStateError(
                f"service policy density completion leaf {obligation_id} "
                "basis/exception registration mismatch")
        rows.append(CompletionObligation(
            obligation_id=obligation_id, layer=LAYER, level="FIELD",
            parent_obligation=parent, family=family, gap_group=gap_group,
            label=label, kind=ENVELOPE_KIND if envelope_def else STATUS_KIND,
            method=method, path=path, caller=caller, operation=operation,
            line_start=line_start, line_end=line_end, source_quote=source_quote,
            assert_line=assert_line, assert_quote=assert_quote,
            request_line=request_line, request_quote=request_quote,
            envelope_def=envelope_def, field=field, comparator=comparator,
            expected=expected, comparator_text=comparator_text,
            expectation=expectation, basis=basis,
            documented_exception=documented_exception, density=True,
            preprod_mode="LIVE_BEARER_REPLAY"))
    if len(rows) != COMPLETION_LEAF_COUNT:
        raise RunStateError("service policy density completion leaf count changed")
    if len({row.obligation_id for row in rows}) != COMPLETION_LEAF_COUNT:
        raise RunStateError("service policy density completion leaf ids are not unique")
    return tuple(rows)


def marker_unit_disposition() -> tuple[dict[str, Any], ...]:
    """Disclose every one of the 23 marker-charged units exactly once."""
    return (
        {"unit": "a", "line": 57, "site": "POST /me/ensure-human",
         "disposition": "flat_wrapper_charge",
         "charged_by": "SERVICE-POLICY.mock-human-bootstrap"},
        {"unit": "b", "line": 58, "site": "GET /register/token",
         "disposition": "derivation_probe_not_charged",
         "charged_by": None,
         "note": "one-time token extraction feeding the register request; "
                 "the R1 not-charged probe convention keeps it out of "
                 "coverage"},
        {"unit": "c", "line": 59, "site": "POST /register",
         "disposition": "flat_wrapper_charge",
         "charged_by": "SERVICE-POLICY.provider-free-bot-registration"},
        {"unit": "d", "line": 64, "site": "POST /bots/onboard",
         "disposition": "bootstrap_registration_plane_bound",
         "charged_by": "SERVICE-POLICY.provider-free-bot-registration",
         "note": "marker-charged request-status unit; its station face is "
                 "bound as a bootstrap operation row under the flat "
                 "registration plane (R1 BOOTSTRAP_OPERATION_ROWS)"},
        {"unit": "e", "line": 70, "site": "POST /groups (object iteration)",
         "disposition": "flat_wrapper_charge",
         "charged_by": "SERVICE-POLICY.legacy-encoding-ignored"},
        {"unit": "f", "line": 70, "site": "POST /groups (null iteration)",
         "disposition": "completion_leaf", "charged_by": "SERVICE-POLICY.F36"},
        {"unit": "g", "line": 78, "site": "GET /groups/{group} (object iteration)",
         "disposition": "completion_leaf", "charged_by": "SERVICE-POLICY.F34"},
        {"unit": "h", "line": 78, "site": "GET /groups/{group} (null iteration)",
         "disposition": "completion_leaf", "charged_by": "SERVICE-POLICY.F38"},
        {"unit": "i", "line": 81,
         "site": "PATCH /groups/{group}/settings (contested 410 unit)",
         "disposition": "contested_flat_charge_recharged_by_completion",
         "charged_by": "SERVICE-POLICY.retired-settings-gone (flat, frozen "
                       "410) and SERVICE-POLICY.F40 (completion recharge, "
                       "documented-exception)",
         "note": "the sim deployment observed 200 against the frozen 410 "
                 "station form; both charges stay visible and neither is "
                 "re-judged (named item " + FORM_DIFFERENCE_ITEM_ID + ")"},
        {"unit": "j", "line": 86, "site": "POST /services/{group}/sessions (run 0)",
         "disposition": "flat_wrapper_charge",
         "charged_by": "SERVICE-POLICY.invocation-lifecycle"},
        {"unit": "k", "line": 86, "site": "POST /services/{group}/sessions (run 1)",
         "disposition": "completion_leaf", "charged_by": "SERVICE-POLICY.F41"},
        {"unit": "l", "line": 93, "site": "GET stored input (run 0)",
         "disposition": "completion_leaf", "charged_by": "SERVICE-POLICY.F42"},
        {"unit": "m", "line": 93, "site": "GET stored input (run 1)",
         "disposition": "completion_leaf", "charged_by": "SERVICE-POLICY.F43"},
        {"unit": "n", "line": 99, "site": "POST reactivation (409)",
         "disposition": "flat_wrapper_charge",
         "charged_by": "SERVICE-POLICY.running-reactivation-denied"},
        {"unit": "o", "line": 102, "site": "GET post-refusal input",
         "disposition": "completion_leaf", "charged_by": "SERVICE-POLICY.F44"},
        {"unit": "p", "line": 105, "site": "POST /groups/{group}/sessions",
         "disposition": "flat_wrapper_charge",
         "charged_by": "SERVICE-POLICY.chat-session-guard"},
        {"unit": "q", "line": 109, "site": "POST /sessions/{sid}/complete",
         "disposition": "completion_leaf", "charged_by": "SERVICE-POLICY.F45"},
        {"unit": "r", "line": 111, "site": "POST conversion refusal (400)",
         "disposition": "completion_leaf", "charged_by": "SERVICE-POLICY.F46"},
        {"unit": "s", "line": 114, "site": "GET final chat Session",
         "disposition": "completion_leaf", "charged_by": "SERVICE-POLICY.F47"},
        {"unit": "t", "line": 77,
         "site": "policy-absent check on created object-encoding Group",
         "disposition": "completion_leaf", "charged_by": "SERVICE-POLICY.F33"},
        {"unit": "u", "line": 78,
         "site": "policy-absent check on read-back object-encoding Group",
         "disposition": "completion_leaf", "charged_by": "SERVICE-POLICY.F35"},
        {"unit": "v", "line": 77,
         "site": "policy-absent check on created null-encoding Group",
         "disposition": "completion_leaf", "charged_by": "SERVICE-POLICY.F37"},
        {"unit": "w", "line": 78,
         "site": "policy-absent check on read-back null-encoding Group",
         "disposition": "completion_leaf", "charged_by": "SERVICE-POLICY.F39"},
    )


def completion_reconciliation() -> dict[str, Any]:
    """Disclose every completion-layer caliber honestly."""
    dispositions = marker_unit_disposition()
    leaves = build_service_policy_density_completion_obligations()
    return {
        "coverage_map_row": "coverage-map/coverage-map-20261031.json no=19 story_retired_service_policy",
        "ledger_formula": "parsed marker checks 23 - flat wrapper obligations 8 = ledger gap 15",
        "ledger_gap": LEDGER_GAP,
        "completion_leaves": COMPLETION_LEAF_COUNT,
        "residual_count_gap": LEDGER_GAP - COMPLETION_LEAF_COUNT,
        "marker_charged_checks": MARKER_CHARGED_CHECKS,
        "marker_unit_dispositions": list(dispositions),
        "marker_unit_disposition_counts": {
            "flat_wrapper_charge": 6,
            "bootstrap_registration_plane_bound": 1,
            "derivation_probe_not_charged": 1,
            "completion_leaf": 14,
            "contested_flat_charge_recharged_by_completion": 1,
        },
        "contested_site": {
            "unit": "i",
            "flat_wrapper_charge": "SERVICE-POLICY.retired-settings-gone (frozen 410 station expectation, byte-identical)",
            "completion_recharge": "SERVICE-POLICY.F40 (basis documented-exception)",
            "statement": (
                "the contested 410 unit keeps both charges visible: the flat "
                "wrapper keeps its frozen station expectation and the "
                "completion leaf registers the sim-observed 200 form as a "
                "documented exception; no row is re-judged and no PASS is "
                "fabricated for the sim form"),
        },
        "form_difference": dict(FORM_DIFFERENCE_NAMED_ITEM),
        "material_gate": dict(MATERIAL_GATE),
        "gap_ledger": {key: dict(value) for key, value in GAP_LEDGER.items()},
        "r1_density_superset_caliber": {
            "parsed_obligation_count": PARSED_OBLIGATION_COUNT,
            "parent_obligation_count": 8,
            "field_obligation_count": FIELD_OBLIGATION_COUNT,
            "statement": "the R1 40-point density superset is a separate caliber and is never merged into the marker-completion accounting",
        },
        "family_density_rows": PARSED_OBLIGATION_COUNT + COMPLETION_LEAF_COUNT,
        "flat_story_checks": MARKER_CHARGED_CHECKS,
        "bearer_leg_story_checks": 20,
        "parsed_unlocated_count": 0,
        "parsed_unlocated": [],
        "upstream_native_assertion_ids": False,
        "sha256_freeze": {
            SOURCE_FILE: STORY_PY_SHA256,
            STORY_SH_FILE: STORY_SH_SHA256,
            "statement": "both upstream story files are SHA-256 frozen; any drift fails the completion builder closed",
        },
        "not_charged_disclosures": [
            {"unit": "b", "line": 58,
             "reason": "one-time token extraction derivation probe; never obligatable as an assertion (R1 NOT_CHARGED_SOURCE_PROBES convention)"},
            {"scope": "teardown dispositions",
             "reason": "the six owned-local teardown dispositions ride the flat cleanup wrapper outside the marker caliber; the completion layer projects no teardown leaf"},
        ],
        "honesty": (
            "every completion leaf anchors to a service_policy_story.py line "
            "span with the verbatim source text and to its feeding request "
            "site; the contested 410 unit is re-charged exactly once under a "
            "documented-exception basis; the sim observation of 200 is "
            "registered evidence, never a silent PASS; the preprod leg stays "
            "an honest NOT_RUN named gap with remote_validation=NOT_RUN and "
            "grants_release_credit=false"),
    }


def completion_plan() -> dict[str, Any]:
    """Return the anchor-validated fifteen-leaf completion plan."""
    leaves = build_service_policy_density_completion_obligations()
    groups: dict[str, int] = {}
    for row in leaves:
        groups[row.gap_group] = groups.get(row.gap_group, 0) + 1
    if tuple(sorted(groups)) != tuple(sorted(GAP_GROUPS)) or any(
            groups[group] != count for group, count in (
                (GAP_GROUP_LEGACY, 7), (GAP_GROUP_RETIRED, 1),
                (GAP_GROUP_INVOCATION, 3), (GAP_GROUP_RUNNING, 1),
                (GAP_GROUP_CONVERSION, 3))):
        raise RunStateError("service policy density completion gap groups changed")
    if sum(1 for row in leaves if row.basis == BASIS_DOCUMENTED_EXCEPTION) != 1:
        raise RunStateError("service policy density completion exception basis count changed")
    return {
        "schema_version": SCHEMA_VERSION,
        "contract": CONTRACT,
        "layer": LAYER,
        "parent": PARENT,
        "case_id": "DUAL-SERVICE-POLICY-DENSITY-01",
        "wrapper_command": DUAL_SERVICE_POLICY_DENSITY_COMMAND,
        "new_registration_command": False,
        "source": {
            "file": SOURCE_FILE,
            "shell_file": STORY_SH_FILE,
            "sha256": STORY_PY_SHA256,
            "shell_sha256": STORY_SH_SHA256,
            "source_read_only": True,
        },
        "wave_token": COMPLETION_WAVE_TOKEN,
        "prior_wave_token": R1_WAVE_TOKEN,
        "accounting": {
            "marker_charged_checks": MARKER_CHARGED_CHECKS,
            "flat_wrapper_obligations": FLAT_WRAPPER_OBLIGATIONS,
            "ledger_gap": LEDGER_GAP,
            "completion_leaves": COMPLETION_LEAF_COUNT,
            "residual_count_gap": RESIDUAL_COUNT_GAP,
            "family_density_rows": PARSED_OBLIGATION_COUNT + COMPLETION_LEAF_COUNT,
        },
        "gap_groups": [
            {"gap_group": group, "leaf_count": groups[group]} for group in GAP_GROUPS
        ],
        "obligations": [row.as_dict() for row in leaves],
        "reconciliation": completion_reconciliation(),
    }


TEARDOWN_DISCLOSURE = TEARDOWN  # family vocabulary parity with the R1 layer
