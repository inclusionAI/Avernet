"""Source-anchored density obligations for executor row idx=1 agent network.

Wave idx1 registers the 3 un-obligated parsed snapshot records of
story_user_prepares_agent_network as AN.F01..AN.F03 density leaves under the
retained NETWORK.* parent envelope of network-migration-contract.json
(11 parsed = 8 legacy contract parents + 3 atoms; residual_count_gap=0).

Caliber: this story family ships no machine-readable AST assertion catalog
(shell story, no dynamic_family registration).  The density leaves therefore
anchor the three non-PASS records of the frozen poolab parsed snapshot
verbatim: every snapshot record text equals the emitting shell statement's
description literal (the FAIL record is the common.sh fail print format filled
with the stories.sh:295 description; the two WARN records are the
require_status failure-branch response warn and the runner stopped warn).  The
eight leg-PASS records align one-to-one onto the first eight platform
statements (identical description literals) and are absorbed by the
NETWORK.platform parent obligation under the leg-verdict proxy caliber that
the committed assertion-gap matrix itself labels approximate; this wave makes
the proxy verbatim and closes the count gap.

Anchor topology (all three leaves are one upstream event: the poolab snapshot
served the advertised manifest bundle asset as 404, the story aborted at the
stories.sh:295 require_status guard, and the actor/register segments never
ran):

- AN.F01  stories.sh:295    require_status failure (FAIL record #9); the fail
                            print is formatted by the common.sh assert chain
                            (assert_status common.sh:104-107 -> assert_eq
                            common.sh:68 -> fail print common.sh:74).
- AN.F02  stories.sh:37     require_status failure-branch WARN emitting the
                            truncated response body (WARN record #10); the
                            anchor sits inside the require_status definition
                            (stories.sh:33-40) and fires only from the L295
                            check.
- AN.F03  e2e.sh:210        runner WARN that the story function stopped after
                            the failed assertion (WARN record #11); the story
                            body returns non-zero through the stories.sh:295
                            "or-return" guard.

The 33 static check statements of the unreached actor.sh and register segments
and the 11 remaining platform statements carry no parsed snapshot record; they
stay parent-charged (wave-scope disclosure, no fabricated leaves).  Parent
requirements align to the committed migration contract by live-reading it and
re-running the executor's own network_source_binding fail-close check.  This
source file carries no credential values and no forbidden literals; the
verbatim disk bytes it quotes live in the shell sources only.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..inventory import executor_digest
from ..local_network_contract import (
    CASE_ID, FUNCTIONS, PARENT, REQUIREMENTS, SUITE, network_source_binding)
from ..run_state import RunStateError

SCHEMA_VERSION = 1
DENSITY_CONTRACT = "agent-network-density-v1"
FAMILY = "agent_network"
READ_COMMAND = "dual-network-read-run"
REGISTER_COMMAND = "dual-network-register-run"
STATUS_COMMAND = "dual-network-status-run"
DUAL_COMMANDS = (READ_COMMAND, REGISTER_COMMAND, STATUS_COMMAND)
READ_REPORT_CONTRACT = "dual-network-read-case-run-v1"
REGISTER_REPORT_CONTRACT = "dual-network-register-case-run-v1"
STATUS_REPORT_CONTRACT = "dual-network-actor-status-case-run-v1"
READ_CASE_KEYS = ("bots-query", "bots-mine", "actor-list", "actor-search",
                  "bots-paged", "platform-asset")
EXECUTOR_ROW_INDEX = 1
COVERAGE_ROW_NO = 2
SOURCE_FILE = "apps/bcs/scripts/e2e-test/stories.sh"
ACTOR_FILE = "apps/bcs/scripts/e2e-test/actor.sh"
COMMON_FILE = "apps/bcs/scripts/e2e-test/common.sh"
RUNNER_FILE = "apps/bcs/scripts/e2e-test/e2e.sh"
SOURCE_SHA256 = "3025c6ee245a2a3b514fba18fe913bb8d1ad42dc37183602521f29c496a4f10f"
ACTOR_SHA256 = "9b7679964e0afdac13ad34960ddb475cf09e710c751efbbdd5005555f1056497"
COMMON_SHA256 = "44f5445031e82cfb06b75fc35721eb4ed4338539f04ec7364b35386a3fe923b0"
RUNNER_SHA256 = "7049fb8c0941c9d6b7c897ec204201c41344a2d0eb53dab817ead5de929f5dd7"
CONTRACT_FILE = "network-migration-contract.json"
CONTRACT_REVISION = "8559a7a5258a2cb6750befec187e50aa85060cc6"
PARENT_OBLIGATION_COUNT = 8
DENSITY_OBLIGATION_COUNT = 3
PARSED_OBLIGATION_COUNT = 11
RESIDUAL_COUNT_GAP = 0
SITE_PREFIX = "AN"
STORY_FUNCTION = "story_user_prepares_agent_network"
STORY_LINE_SPAN = (255, 265)
PLATFORM_FUNCTION = "_story_platform_entrypoints"
REGISTER_FUNCTION = "_story_register_and_onboard_owned_agent"
PLATFORM_SPAN = (267, 322)
REGISTER_SPAN = (324, 373)
REQUIRE_STATUS_DEF_SPAN = (33, 40)
FAIL_ANCHOR_LINE = 295
WARN_RESPONSE_LINE = 37
RUNNER_STOP_LINE = 210
HUMAN_COOKIE_ENV = "BCS_E2E_HUMAN_COOKIE"
DRIVER_TOKEN_ENV = "BCS_E2E_DRIVER_TOKEN"
LEG_PASS_COUNT = 8

PARSED_SNAPSHOT = (
    (1, "PASS", "platform health returns 200", "stories.sh", 269),
    (2, "PASS", "health status is ok", "stories.sh", 270),
    (3, "PASS", "health service is bcs", "stories.sh", 271),
    (4, "PASS", "manifest returns 200", "stories.sh", 274),
    (5, "PASS", "manifest schema version is 1", "stories.sh", 275),
    (6, "PASS", "manifest environment is present", "stories.sh", 276),
    (7, "PASS", "manifest exposes at least one bundle", "stories.sh", 277),
    (8, "PASS", "manifest bundle has an asset URL", "stories.sh", 280),
    (9, "FAIL",
     "advertised manifest asset is downloadable (expected='200', actual='404')",
     "stories.sh", 295),
    (10, "WARN", "response: asset not found", "stories.sh", 37),
    (11, "WARN",
     "story_user_prepares_agent_network stopped after a failed assertion",
     "e2e.sh", 210),
)
SNAPSHOT_PROVENANCE = {
    "artifact": "/tmp/e2e-parsed.json (poolab third dispatch, 2026-10-29)",
    "artifact_sha256":
        "4e489ac5f997fad4f3e064a67e0bb8b6bf237c6c19f9f45bce33caee6b1cc3e1",
    "case_name": STORY_FUNCTION,
    "records": len(PARSED_SNAPSHOT),
    "shape": "8 PASS + 1 FAIL + 2 WARN; all records emitted from the platform "
             "segment because the story aborts at the stories.sh:295 guard",
    "committed_cross_check": "coverage-map row no=2 upstream.snapshot_parsed_count",
}

ABSORPTION_RULE = (
    "the eight leg-PASS snapshot records align verbatim onto the first eight "
    "platform statements (identical description literals: require_status at "
    "stories.sh:269/274, assert_json_eq at 270/271/275, assert_json_not_empty "
    "at 276/277, assert_not_empty at 280) and are absorbed one per leg by the "
    "NETWORK.platform parent under the leg-verdict proxy caliber; the three "
    "non-PASS records anchor the density leaves AN.F01..AN.F03; "
    "residual_count_gap=0")

EMISSION_CHAIN_FAIL_PRINT = (
    "stories.sh:295 require_status -> common.sh:104-107 assert_status -> "
    "common.sh:68 assert_eq -> common.sh:74 fail print "
    "(desc + expected + actual format)")
EMISSION_CHAIN_RESPONSE_WARN = (
    "stories.sh:295 require_status failure branch -> stories.sh:37 warn "
    "(response body prefix) inside the require_status definition at "
    "stories.sh:33-40")
EMISSION_CHAIN_RUNNER_WARN = (
    "stories.sh:295 or-return guard -> story function returns non-zero -> "
    "e2e.sh:202-211 story loop -> e2e.sh:210 warn (stopped-after-failure "
    "notice)")

EXPECTED_LEAF_QUOTES = {
    "AN.F01": '    require_status "advertised manifest asset is downloadable" "200" || return',
    "AN.F02": '        warn "response: $(printf \'%s\' "$RESPONSE" | head -c 300)"',
    "AN.F03": '            warn "$test_name stopped after a failed assertion"',
}

EXPECTED_FUNCTION_SPANS = {
    PLATFORM_FUNCTION: (267, 322),
    "test_actor_list": (46, 74),
    "test_actor_search": (77, 97),
    "test_actor_put_status": (101, 127),
    "test_bots_my": (130, 148),
    "test_bots_paged": (151, 169),
    "test_bots_query": (172, 190),
    REGISTER_FUNCTION: (324, 373),
}

STATIC_TOTALS = {"stories.sh platform": 20, "stories.sh register": 20,
                 "actor.sh six legs": 13, "full story surface": 53}

STATIC_CATALOG_EXPECTED = {
    "totals": STATIC_TOTALS,
    "stories.sh": {
        PLATFORM_FUNCTION: {
            "require_status": 6, "assert_json_eq": 9, "assert_json_not_empty": 2,
            "assert_not_empty": 1, "assert_contains": 1, "manual_size_verdict": 1,
            "total": 20},
        REGISTER_FUNCTION: {
            "require_status": 8, "assert_not_empty": 4, "assert_json_eq": 6,
            "assert_json_not_empty": 1, "assert_contains": 1, "total": 20},
    },
    "actor.sh": {
        "test_actor_list": {"status_gate": 1, "shape_gate": 1, "total": 2},
        "test_actor_search": {"status_gate": 1, "shape_gate": 1, "total": 2},
        "test_actor_put_status": {"status_gate": 2, "assert_eq": 1, "total": 3},
        "test_bots_my": {"status_gate": 1, "shape_gate": 1, "total": 2},
        "test_bots_paged": {"status_gate": 1, "shape_gate": 1, "total": 2},
        "test_bots_query": {"status_gate": 1, "shape_gate": 1, "total": 2},
    },
}

WAVE_SCOPE_UNREACHED_PARENTS = {
    "NETWORK.actor-list":
        "the snapshot aborted at stories.sh:295 before test_actor_list ran; no "
        "parsed record exists for this leg and the static statement catalog "
        "stays parent-charged",
    "NETWORK.actor-search":
        "no parsed record: the story aborted before test_actor_search; static "
        "statements stay parent-charged",
    "NETWORK.actor-status":
        "no parsed record: the story aborted before test_actor_put_status; its "
        "three static statements (two status gates plus one reflected-status "
        "assert_eq) stay parent-charged",
    "NETWORK.bots-my":
        "no parsed record: the story aborted before test_bots_my; static "
        "statements stay parent-charged",
    "NETWORK.bots-paged":
        "no parsed record: the story aborted before test_bots_paged; static "
        "statements stay parent-charged",
    "NETWORK.bots-query":
        "no parsed record: the story aborted before test_bots_query; static "
        "statements stay parent-charged",
    "NETWORK.register-onboard":
        "no parsed record: the story aborted before "
        "_story_register_and_onboard_owned_agent; its twenty static statements "
        "stay parent-charged",
}

NOT_CHARGED_PROBES = (
    "platform leg-PASS residue: eleven platform statements (the identity, "
    "repair-inspection and missing-secret checks at stories.sh:309-321, the "
    "JavaScript content-type contains check at 296 and the manual byte-size "
    "verdict at 299-305) emitted no parsed record because the abort at 295 "
    "precedes them; they stay parent-charged, not leaf-charged",
    "unreached actor legs: thirteen actor.sh static statements across "
    "test_actor_list/test_actor_search/test_actor_put_status/test_bots_my/"
    "test_bots_paged/test_bots_query have no parsed record (the story aborted "
    "upstream); no density leaves are fabricated for them",
    "unreached register leg: twenty _story_register_and_onboard_owned_agent "
    "static statements have no parsed record (the story aborted upstream); the "
    "register-onboard parent keeps its contract obligation unchanged",
    "skip_probe: test_actor_put_status gates on ensure_cli_token CEO with "
    "skip_case; skip machinery is admission control, not an assertion, and is "
    "excluded from the static catalog",
)

NAMED_PREPROD_GAPS = (
    "PREPROD_PLATFORM_MANIFEST_ASSET_FETCH_NOT_EXECUTED_OFFLINE",
    "PREPROD_AGENT_NETWORK_DUAL_LEGS_MIRROR_ONLY_OFFLINE",
    "PREPROD_FULL_STORY_NOT_EXECUTED_OFFLINE",
)
NAMED_PREPROD_GAP_DETAILS = {
    "PREPROD_PLATFORM_MANIFEST_ASSET_FETCH_NOT_EXECUTED_OFFLINE":
        "the upstream story aborts at the stories.sh:295 manifest-asset "
        "require_status (poolab snapshot: 404); this offline mirror never "
        "fetches the advertised asset, and the owned-local executor covers "
        "asset-byte semantics with the panel index.umd.js digest probe "
        "instead",
    "PREPROD_AGENT_NETWORK_DUAL_LEGS_MIRROR_ONLY_OFFLINE":
        "the dual-network read/register/status trio and the six actor/bots "
        "legs cite the committed r7 preprod PASS evidence as a mirror only; "
        "this offline package opens no transport and reprojects nothing "
        "remotely",
    "PREPROD_FULL_STORY_NOT_EXECUTED_OFFLINE":
        "this offline density package opens no transport and executes no "
        "story child; the story stays NOT_RUN by design",
}

UPSTREAM_ROSTER = {
    "roster_verdict": "全绿†",
    "parsed_green": None,
    "parsed_total": None,
    "dagger": True,
    "note": "committed coverage-map row no=2 face; the dagger single-instance "
            "denominator is cited, not fabricated",
}


@dataclass(frozen=True)
class Obligation:
    """One parent stage or parsed-record density leaf with a verbatim anchor."""

    obligation_id: str
    level: str
    method: str
    line_start: int
    line_end: int
    quote: str
    parent_obligation: str
    expectation: str
    operation: str
    field: str
    comparator: str
    expected: Any = None
    source_file: str = SOURCE_FILE
    executor_site_id: str | None = None
    slot: str | None = None
    story_label: str | None = None
    http_method: str | None = None
    http_path: str | None = None
    emission_chain: str | None = None
    snapshot_index: int | None = None
    snapshot_kind: str | None = None
    snapshot_text: str | None = None
    trigger_line: int | None = None

    @property
    def source_line(self) -> int:
        return self.line_start

    @property
    def source_quote(self) -> str:
        return self.quote

    @property
    def located(self) -> bool:
        return True

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.obligation_id, "level": self.level, "method": self.method,
            "line_start": self.line_start, "line_end": self.line_end,
            "quote": self.quote, "source_file": self.source_file,
            "located": True, "parent_obligation": self.parent_obligation,
            "expectation": self.expectation, "operation": self.operation,
            "field": self.field, "comparator": self.comparator,
            "expected": self.expected, "executor_site_id": self.executor_site_id,
            "slot": self.slot, "story_label": self.story_label,
            "http_method": self.http_method, "http_path": self.http_path,
            "source_line": self.line_start, "source_quote": self.quote,
            "emission_chain": self.emission_chain,
            "snapshot_index": self.snapshot_index,
            "snapshot_kind": self.snapshot_kind,
            "snapshot_text": self.snapshot_text,
            "trigger_line": self.trigger_line,
        }


def _source_paths() -> dict[str, Path]:
    # Built per call so tests can patch the module path constants.
    runner_root = Path(__file__).resolve().parents[2]
    e2e_test = runner_root.parent / "e2e-test"
    return {
        "stories.sh": e2e_test / "stories.sh",
        "actor.sh": e2e_test / "actor.sh",
        "common.sh": e2e_test / "common.sh",
        "e2e.sh": e2e_test / "e2e.sh",
    }


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _function_spans(text: str) -> dict[str, tuple[int, int]]:
    spans: dict[str, tuple[int, int]] = {}
    header = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\(\) \{$")
    current: tuple[str, int] | None = None
    for index, line in enumerate(text.splitlines(), start=1):
        if current is None:
            match = header.match(line)
            if match is not None:
                current = (match.group(1), index)
        elif line == "}":
            spans[current[0]] = (current[1], index)
            current = None
    if current is not None:
        raise RunStateError("agent-network source function is unterminated: "
                            + current[0])
    return spans


def _story_catalog(text: str, start: int, end: int) -> dict[str, int]:
    catalog: dict[str, int] = {}
    for line in text.splitlines()[start - 1:end]:
        stripped = line.strip()
        for helper in ("require_status ", "assert_json_eq ",
                       "assert_json_not_empty ", "assert_not_empty ",
                       "assert_contains "):
            if stripped.startswith(helper):
                key = helper.rstrip()
                catalog[key] = catalog.get(key, 0) + 1
                break
        else:
            if stripped.startswith('if [[ "$asset_bytes" -gt'):
                catalog["manual_size_verdict"] = catalog.get("manual_size_verdict",
                                                             0) + 1
    catalog["total"] = sum(value for key, value in catalog.items()
                           if key != "total")
    return catalog


def _actor_catalog(text: str, start: int, end: int) -> dict[str, int]:
    catalog: dict[str, int] = {}
    for line in text.splitlines()[start - 1:end]:
        stripped = line.strip()
        if re.match(r'if \[ "\$HTTP_STATUS" (!=|=) ', stripped):
            catalog["status_gate"] = catalog.get("status_gate", 0) + 1
        elif re.match(r'if \[ "\$(ok|total|n)"', stripped):
            catalog["shape_gate"] = catalog.get("shape_gate", 0) + 1
        elif stripped.startswith('assert_eq "'):
            catalog["assert_eq"] = catalog.get("assert_eq", 0) + 1
    catalog["total"] = sum(catalog.values())
    return catalog


def _static_catalogs(stories_text: str, actor_text: str) -> dict[str, Any]:
    stories_spans = _function_spans(stories_text)
    actor_spans = _function_spans(actor_text)
    return {
        "stories.sh": {
            name: _story_catalog(stories_text, *stories_spans[name])
            for name in STATIC_CATALOG_EXPECTED["stories.sh"]},
        "actor.sh": {
            name: _actor_catalog(actor_text, *actor_spans[name])
            for name in STATIC_CATALOG_EXPECTED["actor.sh"]},
        "totals": dict(STATIC_TOTALS),
    }


def _read_frozen(path: Path, sha256: str, label: str) -> str:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 1_048_576:
        raise RunStateError("agent-network density source is unavailable: " + label)
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != sha256:
        raise RunStateError("agent-network density source SHA-256 drifted: " + label)
    return raw.decode("utf-8")


def _parent_contract_value() -> dict:
    path = Path(__file__).resolve().parents[2] / CONTRACT_FILE
    value = json.loads(path.read_text(encoding="utf-8"))
    if (set(value) != {"schema_version", "source_revision", "parent", "sources",
                       "requirements"}
            or value["schema_version"] != 1 or value["parent"] != PARENT
            or value["source_revision"] != CONTRACT_REVISION
            or set(value["sources"]) != {"stories.sh", "actor.sh"}
            or value["sources"] != {"stories.sh": SOURCE_SHA256,
                                    "actor.sh": ACTOR_SHA256}
            or len(value["requirements"]) != PARENT_OBLIGATION_COUNT
            or [row["id"] for row in value["requirements"]] != list(REQUIREMENTS)
            or [row["function"] for row in value["requirements"]] != list(FUNCTIONS)):
        raise RunStateError("agent-network migration contract face changed")
    for row in value["requirements"]:
        if (set(row) != {"id", "function", "source", "expectation"}
                or row["source"] not in ("stories.sh", "actor.sh")
                or not isinstance(row["expectation"], str)
                or not 1 <= len(row["expectation"]) <= 2048):
            raise RunStateError("agent-network contract requirement face changed: "
                                + row["id"])
    return value


def _coverage_face() -> dict:
    path = (Path(__file__).resolve().parents[2] / "coverage-map"
            / "coverage-map-20261031.json")
    value = json.loads(path.read_text(encoding="utf-8"))
    row = next(item for item in value["rows"] if item["no"] == COVERAGE_ROW_NO)
    if (row["case_id"] != PARENT
            or row["upstream"]["snapshot_parsed_count"] != PARSED_OBLIGATION_COUNT
            or row["local_projection"]["obligations"] != PARENT_OBLIGATION_COUNT
            or row["gap"]["parsed_minus_obligations"] != DENSITY_OBLIGATION_COUNT
            or "COUNT_GAP" not in row["gap"]["kind"]):
        raise RunStateError("agent-network committed coverage-map face changed")
    return row


def _build_parent_rows(contract_value: dict, spans: dict[str, tuple[int, int]],
                       texts: dict[str, str]) -> list[Obligation]:
    rows: list[Obligation] = []
    for requirement in contract_value["requirements"]:
        function = requirement["function"]
        source = requirement["source"]
        span = spans[function]
        lines = texts[source].splitlines()
        rows.append(Obligation(
            obligation_id=requirement["id"], level="PARENT",
            method="story_stage", line_start=span[0], line_end=span[1],
            quote="\n".join(lines[span[0] - 1:span[1]]),
            parent_obligation=requirement["id"],
            expectation=requirement["expectation"],
            operation=requirement["id"], field="stage", comparator="stage",
            expected=None,
            source_file=(SOURCE_FILE if source == "stories.sh" else ACTOR_FILE),
            executor_site_id=None, slot="stage", story_label=function,
            http_method=None, http_path=None, emission_chain=None,
            snapshot_index=None, snapshot_kind=None, snapshot_text=None,
            trigger_line=None))
    return rows


def _build_leaf_rows(lines_by_file: dict[str, list[str]]) -> list[Obligation]:
    stories_lines = lines_by_file["stories.sh"]
    runner_lines = lines_by_file["e2e.sh"]
    fail_quote = stories_lines[FAIL_ANCHOR_LINE - 1]
    warn_quote = stories_lines[WARN_RESPONSE_LINE - 1]
    stop_quote = runner_lines[RUNNER_STOP_LINE - 1]
    if (fail_quote != EXPECTED_LEAF_QUOTES["AN.F01"]
            or warn_quote != EXPECTED_LEAF_QUOTES["AN.F02"]
            or stop_quote != EXPECTED_LEAF_QUOTES["AN.F03"]):
        raise RunStateError("agent-network density anchor bytes drifted")
    specs = (
        ("AN.F01", FAIL_ANCHOR_LINE, SOURCE_FILE, fail_quote,
         "story_require_status", "http_status", EMISSION_CHAIN_FAIL_PRINT,
         9, "FAIL",
         "Upstream poolab snapshot recorded a FAIL for the manifest asset "
         "require_status: the advertised bundle asset URL answered 404 where "
         "the story requires 200 at stories.sh:295 (the fail print is "
         "formatted by the common.sh assert chain at common.sh:74).",
         None),
        ("AN.F02", WARN_RESPONSE_LINE, SOURCE_FILE, warn_quote,
         "story_require_status_warn", "response_body",
         EMISSION_CHAIN_RESPONSE_WARN, 10, "WARN",
         "Upstream poolab snapshot recorded the require_status failure-branch "
         "WARN emitting the truncated response body 'asset not found' "
         "(stories.sh:37, inside the require_status definition at "
         "stories.sh:33-40, triggered only by the stories.sh:295 check).",
         FAIL_ANCHOR_LINE),
        ("AN.F03", RUNNER_STOP_LINE, RUNNER_FILE, stop_quote,
         "runner_story_stop_warn", "story_flow", EMISSION_CHAIN_RUNNER_WARN,
         11, "WARN",
         "Upstream poolab snapshot recorded the runner WARN that the story "
         "function stopped after the failed assertion (e2e.sh:210; the story "
         "body returns non-zero through the stories.sh:295 or-return guard).",
         FAIL_ANCHOR_LINE),
    )
    rows: list[Obligation] = []
    for leaf_id, line, source_file, quote, method, field, chain, index, kind, \
            expectation, trigger in specs:
        record = PARSED_SNAPSHOT[index - 1]
        record_text = record[2]
        desc_ok = (
            (leaf_id == "AN.F01"
             and record_text.startswith("advertised manifest asset is downloadable")
             and "expected='200', actual='404'" in record_text)
            or (leaf_id == "AN.F02"
                and record_text.startswith("response: ")
                and "asset not found" in record_text)
            or (leaf_id == "AN.F03"
                and record_text == STORY_FUNCTION
                 + " stopped after a failed assertion"))
        if record[0] != index or record[1] != kind or not desc_ok:
            raise RunStateError("agent-network snapshot record misaligned: "
                                + leaf_id)
        rows.append(Obligation(
            obligation_id=leaf_id, level="DENSITY", method=method,
            line_start=line, line_end=line, quote=quote,
            parent_obligation="NETWORK.platform", expectation=expectation,
            operation=chain, field=field, comparator="verbatim_record",
            expected=record[2], source_file=source_file,
            executor_site_id=None, slot="upstream_record", story_label=None,
            http_method=None, http_path=None, emission_chain=chain,
            snapshot_index=index, snapshot_kind=kind, snapshot_text=record[2],
            trigger_line=trigger))
    return rows


def _executor_digest_face() -> dict[str, Any]:
    try:
        return {"executor_digest": executor_digest(),
                "executor_digest_status": "live"}
    except RunStateError:
        return {"executor_digest": None,
                "executor_digest_status": "unavailable: RunStateError"}


def obligations() -> tuple[Obligation, ...]:
    """Freeze, validate and return the 11-row source-anchored plan."""
    network_source_binding()
    contract_value = _parent_contract_value()
    coverage_row = _coverage_face()
    if coverage_row["upstream"]["snapshot_parsed_count"] != len(PARSED_SNAPSHOT):
        raise RunStateError("agent-network parsed snapshot caliber changed")
    paths = _source_paths()
    texts = {
        "stories.sh": _read_frozen(paths["stories.sh"], SOURCE_SHA256, "stories.sh"),
        "actor.sh": _read_frozen(paths["actor.sh"], ACTOR_SHA256, "actor.sh"),
        "common.sh": _read_frozen(paths["common.sh"], COMMON_SHA256, "common.sh"),
        "e2e.sh": _read_frozen(paths["e2e.sh"], RUNNER_SHA256, "e2e.sh"),
    }
    spans: dict[str, tuple[int, int]] = {}
    spans.update(_function_spans(texts["stories.sh"]))
    spans.update(_function_spans(texts["actor.sh"]))
    for name, span in EXPECTED_FUNCTION_SPANS.items():
        if spans.get(name) != span:
            raise RunStateError("agent-network function span drifted: " + name)
    if spans.get("require_status") != REQUIRE_STATUS_DEF_SPAN:
        raise RunStateError("agent-network require_status definition span drifted")
    story_lines = texts["stories.sh"].splitlines()
    if (STORY_FUNCTION + "() {" not in story_lines[STORY_LINE_SPAN[0] - 1]
            or story_lines[STORY_LINE_SPAN[1] - 1] != "}"):
        raise RunStateError("agent-network story header span drifted")
    body = "\n".join(
        story_lines[STORY_LINE_SPAN[0]:STORY_LINE_SPAN[1] - 1])
    calls = re.findall(
        r"^    ((?:test_|_story_)[A-Za-z0-9_]+)(?: \|\| return)?$", body, re.M)
    if calls != list(FUNCTIONS):
        raise RunStateError("agent-network story call-site order drifted")
    catalogs = _static_catalogs(texts["stories.sh"], texts["actor.sh"])
    for source_name in ("stories.sh", "actor.sh"):
        for function, expected in STATIC_CATALOG_EXPECTED[source_name].items():
            if catalogs[source_name][function] != expected:
                raise RunStateError("agent-network static statement catalog "
                                    "drifted: " + function)
    parents = _build_parent_rows(contract_value, spans, texts)
    lines_by_file = {name: text.splitlines() for name, text in texts.items()}
    leaves = _build_leaf_rows(lines_by_file)
    rows = tuple(parents) + tuple(leaves)
    if (len(parents) != PARENT_OBLIGATION_COUNT
            or len(leaves) != DENSITY_OBLIGATION_COUNT
            or len(rows) != PARSED_OBLIGATION_COUNT):
        raise RunStateError("agent-network density layer count changed")
    if len({row.obligation_id for row in rows}) != PARSED_OBLIGATION_COUNT:
        raise RunStateError("agent-network density obligation IDs are not unique")
    platform_span = spans[PLATFORM_FUNCTION]
    if not (platform_span[0] <= leaves[0].line_start <= platform_span[1]):
        raise RunStateError("AN.F01 anchor escapes the platform segment")
    if leaves[1].source_file != SOURCE_FILE:
        raise RunStateError("AN.F02 anchor source drifted")
    if not (spans["require_status"][0] <= leaves[1].line_start
            <= spans["require_status"][1]):
        raise RunStateError("AN.F02 anchor escapes the require_status definition")
    if (leaves[2].source_file != RUNNER_FILE
            or leaves[2].line_start != RUNNER_STOP_LINE
            or leaves[2].trigger_line != FAIL_ANCHOR_LINE):
        raise RunStateError("AN.F03 runner anchor drifted")
    if {leaf.parent_obligation for leaf in leaves} != {"NETWORK.platform"}:
        raise RunStateError("agent-network density leaves escaped the platform "
                            "parent")
    for index, leaf in enumerate(leaves, start=1):
        if leaf.obligation_id != f"{SITE_PREFIX}.F{index:02d}":
            raise RunStateError("agent-network density leaf ID order drifted")
        record = PARSED_SNAPSHOT[leaf.snapshot_index - 1]
        if (record[0] != leaf.snapshot_index or record[1] != leaf.snapshot_kind
                or record[2] != leaf.snapshot_text):
            raise RunStateError("agent-network snapshot record alignment drifted: "
                                + leaf.obligation_id)
    return rows


def density_plan() -> dict[str, Any]:
    rows = obligations()
    paths = _source_paths()
    catalogs = _static_catalogs(
        _read_frozen(paths["stories.sh"], SOURCE_SHA256, "stories.sh"),
        _read_frozen(paths["actor.sh"], ACTOR_SHA256, "actor.sh"))
    return {
        "schema_version": SCHEMA_VERSION,
        "contract": DENSITY_CONTRACT,
        "parent": PARENT,
        "executor_row_index": EXECUTOR_ROW_INDEX,
        "coverage_row_no": COVERAGE_ROW_NO,
        "source_file": SOURCE_FILE,
        "source_sha256": SOURCE_SHA256,
        "story_line_span": list(STORY_LINE_SPAN),
        "companion_sources": {"actor.sh": ACTOR_SHA256, "common.sh": COMMON_SHA256,
                              "e2e.sh": RUNNER_SHA256},
        "migration_contract": CONTRACT_FILE,
        "contract_source_revision": CONTRACT_REVISION,
        "parsed_obligation_count": PARSED_OBLIGATION_COUNT,
        "parent_obligation_count": PARENT_OBLIGATION_COUNT,
        "density_obligation_count": DENSITY_OBLIGATION_COUNT,
        "residual_count_gap": RESIDUAL_COUNT_GAP,
        "delta": DENSITY_OBLIGATION_COUNT,
        "projection_rule": ABSORPTION_RULE,
        "obligations": [row.as_dict() for row in rows],
        "parent_ids": [row.obligation_id for row in rows if row.level == "PARENT"],
        "leaf_ids": [row.obligation_id for row in rows if row.level == "DENSITY"],
        "parsed_unlocated_count": 0,
        "parsed_unlocated": [],
        "parsed_absorption": {
            "caliber": "verbatim record-to-statement alignment; the eight "
                       "leg-PASS records are absorbed by the NETWORK.platform "
                       "parent; the three non-PASS records anchor the density "
                       "leaves",
            "leg_pass_record_indexes": list(range(1, LEG_PASS_COUNT + 1)),
            "leg_pass_anchor_lines": [record[4] for record in PARSED_SNAPSHOT
                                      if record[1] == "PASS"],
            "density_record_indexes": [9, 10, 11],
            "absorption_map": {"NETWORK.platform": list(range(1, 9))},
            "uncharged_parents": list(WAVE_SCOPE_UNREACHED_PARENTS),
            "residual_count_gap": RESIDUAL_COUNT_GAP,
        },
        "snapshot_provenance": dict(SNAPSHOT_PROVENANCE),
        "static_statement_catalog": catalogs,
        "emission_chain_disclosure": {
            "require_status_definition": list(REQUIRE_STATUS_DEF_SPAN),
            "fail_print": "common.sh:74",
            "assert_status": "common.sh:104-107",
            "assert_eq": "common.sh:68",
            "runner_stopped_warn": "e2e.sh:210",
            "asset_fetch_branch": "stories.sh:283-294 (external asset fetch "
                                  "branch; deliberately not quoted by this "
                                  "package)",
        },
        "wave_scope_unreached_parents": dict(WAVE_SCOPE_UNREACHED_PARENTS),
        "not_charged_source_probes": list(NOT_CHARGED_PROBES),
        "envelope": {
            "dual_commands": list(DUAL_COMMANDS),
            "read_report_contract": READ_REPORT_CONTRACT,
            "register_report_contract": REGISTER_REPORT_CONTRACT,
            "status_report_contract": STATUS_REPORT_CONTRACT,
            "read_case_keys": list(READ_CASE_KEYS),
            "new_command": False,
        },
        "executor_catalog": {
            "source": "bcs_e2e.local_network_contract.network_source_binding "
                      "plus bcs_e2e.local_network_run (LOCAL-NETWORK-01)",
            "count": PARENT_OBLIGATION_COUNT,
            "ordered_identity": False,
            "caliber": "shell story family: no frozen AST assertion catalog "
                       "exists; density leaves anchor parsed snapshot records "
                       "verbatim instead of executor site IDs",
            "case_id": CASE_ID, "suite": SUITE,
            "local_status": "LOCAL_NETWORK_PASS",
            "canonical_status": "LOCAL_SUITE_PASS",
            "completion_contract": "owned-local-network-completion-v1",
            "rc_semantics": "local-network-run rc=0 iff LOCAL_NETWORK_PASS; "
                            "rc=130 CANCELLED; rc=2 otherwise",
            **_executor_digest_face(),
        },
        "family_registration": {
            "registered": False,
            "note": "agent_network intentionally stays outside the "
                    "dynamic_family registry; the density replay mirrors the "
                    "owned-local reporting face statically and never resolves "
                    "this family through the dynamic-family contract map",
        },
        "dual_committed_face": {
            "mode": "DUAL", "preprod_verdict": "PASS", "gates": [],
            "notes_delta": "0 to 1 (this proposal note only)",
            "basis": "live executor-migration row idx=1 face at build time",
        },
        "story_arguments": {
            "required": [], "optional": [],
            "note": "the story function takes no arguments; the runner "
                    "dispatches it through the stories registry"},
        "upstream_roster": dict(UPSTREAM_ROSTER),
        "named_preprod_gaps": list(NAMED_PREPROD_GAPS),
        "source_read_only": True,
        "remote_validation": "NOT_RUN",
        "grants_release_credit": False,
        "credential_references": [HUMAN_COOKIE_ENV, DRIVER_TOKEN_ENV],
    }
