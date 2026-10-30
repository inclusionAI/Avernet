"""Load-time fences and dual-environment projections for the 131 density leaves.

Wave D4 density backfill for roster case #20
(``story_provider_operator_manages_subbots``).  The legacy roster parsed one
shell wrapper that embeds a self-checking Python child executing 131 live HTTP
checks without obligation ids, while the frozen owned-local contract carries
only 15 coarse obligations.  ``provider-subbots-density-obligations.json``
obligationizes every executed check as exactly one density leaf.  This module
is the honest consumer side of that frozen contract:

* ``load_density_contract`` re-validates the emitted JSON against the frozen
  migration contract, the pinned upstream digests, the wrapper shape and the
  checks.py evidence vocabulary, without git and without network access;
* ``project_local_density`` turns a real owned-local child receipt into a
  131-leaf PASS projection only through the frozen collective criterion and
  otherwise marks every leaf honestly NOT_RUN;
* ``project_preprod_density`` emits the BLOCKED_INVENTORIED receipt with three
  named documented gaps and network_requests=0, so no remote PASS is ever
  fabricated.

Credential hygiene: only environment variable NAMES appear here
(``AVERNET_SECRET_PRINCIPAL_SIGNING_KEY_VALUE`` inside the documented gap
text); no credential value is ever read, resolved or persisted.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from ..profile import ConfigError, unique_keys
from ..run_state import RunStateError
from .contract import (
    ANCHORS,
    CASE_ID,
    DOC_RELATIVE,
    PARENT,
    PRODUCT_DOC,
    REQUIREMENTS,
    SHELL_RELATIVE,
    SHELL_SOURCE,
    STATUS as LOCAL_STATUS,
    STORY_CHECKS,
    STORY_MARKERS,
    STORY_RELATIVE,
    STORY_SOURCE,
    SUITE,
)

ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = ROOT.parents[3]
FROZEN_CONTRACT = ROOT / "provider-subbots-migration-contract.json"
CHECKS_MODULE = ROOT / "bcs_e2e" / "provider_subbots" / "checks.py"
MIRROR_STORY = Path(__file__).with_name("story.py")
DENSITY_CONTRACT_FILE = "provider-subbots-density-obligations.json"
DENSITY_CONTRACT_PATH = Path(__file__).with_name(DENSITY_CONTRACT_FILE)
DENSITY_CONTRACT_ID = "provider-subbots-density-obligations-v1"
DENSITY_PREFIX = "PROVIDER-SUBBOTS-DENSITY"
DENSITY_LEAVES = 131
DENSITY_FAMILIES = 15
STATUS_NOT_RUN = "NOT_RUN"
STATUS_BLOCKED_INVENTORIED = "BLOCKED_INVENTORIED"
GAP_CONTROL_PLANE = "PREPROD_PROVIDER_CONTROL_PLANE_ABSENT"
GAP_SIGNING_KEY = "PREPROD_PROVIDER_SUBBOTS_PRINCIPAL_SIGNING_KEY_ABSENT"
GAP_ADMIN_CHANNEL = "PREPROD_PROVIDER_SUBBOTS_ADMIN_CREDENTIAL_CHANNEL_ABSENT"
LOCAL_DENSITY_GAP = "LOCAL_SUBBOTS_CHILD_RECEIPT_INCOMPLETE"
LOCAL_EXECUTOR = "owned-local-child-story"
LOCAL_DERIVATION = "FROZEN_CHILD_RECEIPT_COLLECTIVE"
WRAPPER_FUNCTION = "story_provider_operator_manages_subbots"
WRAPPER_LINES = (97, 103)
WRAPPER_CHILD = "agentpass_subbots_story.py"
WRAPPER_MARKER = (
    'assert_eq "subbots registration and runtime identity journey" "$result" "0"')
VIEW_PATH = "/api/v1/collaboration/bots/me"
VIEW_GROUP_LINES = frozenset({274, 275, 276})
VIEW_SUCCESS_EXPANSIONS = frozenset({
    "status", "cache_control", "registered", "parent_matches", "bot_id_matches",
    "ref_matches", "token_absent",
})
VIEW_REJECTION_EXPANSIONS = frozenset({"status", "cache_control"})
SIGNED_PRINCIPAL_LINES = frozenset({408, 414, 433, 448})
ADMIN_CHANNEL_LINES = frozenset(
    {175, 183, 204, 307, 411, 430, 440, 455, 469, 476})
STATIC_MULTIPLICITY = {175: 6, 234: 4, 277: 2, 284: 2, 338: 4, 274: 2, 275: 2, 276: 2}
# Independent fence counters: they must match the emitted contract, but they are
# re-declared here so a builder regression cannot silence this loader.
DENSITY_FAMILY_COUNTS = {
    "provider-registration-discovery": 3,
    "admin-complete-ref-validation": 6,
    "admin-sibling-registration": 3,
    "scoped-complete-ref-validation": 6,
    "scoped-sibling-distinct-identity": 4,
    "duplicate-reference-conflicts": 2,
    "self-lookup-actor-binding": 34,
    "legacy-api-actor-binding": 4,
    "metadata-update-parent-immutability": 9,
    "onboard-parent-immutability": 8,
    "parent-discovery-fail-closed": 7,
    "callback-actor-binding": 4,
    "signed-principal-live-state": 1,
    "disable-enable-state": 23,
    "deletion-tombstone-retirement": 17,
}
DENSITY_KIND_COUNTS = {"request-status": 47, "self-view": 75, "semantic": 9}
DENSITY_BASIS_COUNTS = {
    "frozen-anchor": 66, "story-lines-range": 55,
    "documented-exception": 1, "density-semantic-declaration": 9,
}
DENSITY_REFINEMENT_COUNTS = {GAP_SIGNING_KEY: 4, GAP_ADMIN_CHANNEL: 15}
DENSITY_METHODS = frozenset({"GET", "POST", "PUT", "DELETE", "PATCH"})
DENSITY_VIEW_ACTOR_KINDS = frozenset({"none", "matching", "sibling"})
RELATIVE_KEYS = {
    "agentpass-subbots.md": "product_doc",
    "agentpass_subbots_story.py": "story",
    "e2e.sh": "wrapper",
}

_TOP_KEYS = frozenset({
    "accounting", "contract", "density_prefix", "local", "obligations", "parent",
    "parent_projection", "preprod", "projection_rules", "roster", "schema_version",
    "source_case_id", "source_revision", "sources", "sources_relative",
    "story_checks", "suite", "wrapper",
})
_LEAF_KEYS = frozenset({
    "id", "check_no", "family", "parent", "kind", "subkind", "title",
    "upstream_label", "expectation", "method", "path", "expected_status",
    "actor", "actor_headers", "anchor", "projection", "evidence", "local",
    "preprod",
})
_ANCHOR_KEYS = frozenset({
    "expansion", "file", "label_literal", "loop_index", "loop_values",
    "multiplicity", "site_end_line", "site_line", "site_slice",
    "view_actor_kind", "view_bot", "view_context", "view_execution",
})
_EVIDENCE_KEYS = frozenset({
    "collective_marker", "primary_segment", "primary_path",
    "cross_segment_validation",
})
_FROZEN_KEYS = frozenset({
    "schema_version", "parent", "suite", "case_id", "environment", "sources",
    "source_revision", "source_file_revisions", "story_checks", "story_markers",
    "owned_runtime_binary", "config_fragment", "redaction_tokens", "requirements",
    "documented_gaps", "adaptability", "remote_validation", "grants_release_credit",
    "release_parent_denominator", "independent_slice", "evidence_granularity",
})
_ID_PATTERN = re.compile(r"PROVIDER-SUBBOTS-DENSITY\.[a-z0-9-]+\.[0-9]{3}")
_HEX40 = re.compile(r"[0-9a-f]{40}")
_HEX64 = re.compile(r"[0-9a-f]{64}")
_SOURCE_NAMES = ("agentpass-subbots.md", "agentpass_subbots_story.py", "e2e.sh")
_ABSOLUTE_SOURCES = {
    "agentpass-subbots.md": PRODUCT_DOC,
    "agentpass_subbots_story.py": STORY_SOURCE,
    "e2e.sh": SHELL_SOURCE,
}


class DensityContractError(RunStateError):
    """The density contract, its pinned sources, or a projection input drifted."""


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _slice(text: str, start: int, end: int) -> str:
    lines = text.splitlines()
    return "\n".join(lines[start - 1:end])


def _bounded_source(path: Path, where: str) -> str:
    if path.is_symlink() or not path.is_file():
        raise DensityContractError(where + ": pinned source is not a regular file")
    if path.stat().st_size > 1_048_576:
        raise DensityContractError(where + ": pinned source exceeded its read bound")
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        raise DensityContractError(where + ": pinned source is unreadable") from None


def _load_json(path: Path, where: str) -> dict:
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        raise DensityContractError(where + ": unreadable JSON source") from None
    try:
        return json.loads(raw, object_pairs_hook=unique_keys)
    except (ConfigError, json.JSONDecodeError):
        raise DensityContractError(where + ": malformed JSON or duplicate JSON key") from None


def _exact_keys(value: object, keys: frozenset, where: str) -> dict:
    if not isinstance(value, dict) or set(value) != set(keys):
        raise DensityContractError(where + ": missing or unknown fields")
    return value


def _two_int_lines(value: object, where: str) -> tuple[int, int]:
    if (not isinstance(value, list) or len(value) != 2
            or any(type(item) is not int for item in value)
            or not 1 <= value[0] <= value[1]):
        raise DensityContractError(where + ": story_lines range is invalid")
    return value[0], value[1]


def _frozen_indexes() -> tuple[dict[str, list[int]], str]:
    """Return the frozen parent ranges and the frozen source revision."""
    frozen = _load_json(FROZEN_CONTRACT, "frozen migration contract")
    _exact_keys(frozen, _FROZEN_KEYS, "frozen migration contract")
    if frozen["schema_version"] != 1 or frozen["parent"] != PARENT:
        raise DensityContractError("frozen migration contract identity changed")
    if frozen["story_checks"] != STORY_CHECKS:
        raise DensityContractError("frozen migration contract check denominator changed")
    requirements = frozen["requirements"]
    if [row.get("id") for row in requirements] != list(REQUIREMENTS):
        raise DensityContractError("frozen requirement order changed")
    ranges: dict[str, list[int]] = {}
    for row in requirements:
        _exact_keys(row, frozenset({"id", "expectation", "story_lines", "anchors", "markers"}),
                    "frozen requirement")
        low, high = _two_int_lines(row["story_lines"], "frozen requirement " + row["id"])
        ranges[row["id"]] = [low, high]
    revision = frozen["source_revision"]
    if not isinstance(revision, str) or _HEX40.fullmatch(revision) is None:
        raise DensityContractError("frozen source revision is not a full commit digest")
    return ranges, revision


def _evidence_vocabulary() -> tuple[frozenset, frozenset]:
    """Extract the honest segment and field vocabulary from checks.py text."""
    text = _bounded_source(CHECKS_MODULE, "checks.py")
    segments = frozenset(re.findall(r"evidence\.get\('([a-z_]+)'\)", text))
    vocabulary = frozenset(re.findall(r"'([a-z][a-z0-9_]*)'", text))
    if not segments:
        raise DensityContractError("checks.py no longer exposes evidence segments")
    return segments, vocabulary


def load_density_contract(path: Path | None = None) -> dict:
    """Load and fully re-validate the 131-leaf density contract, offline."""
    contract_path = DENSITY_CONTRACT_PATH if path is None else Path(path)
    try:
        raw = contract_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        raise DensityContractError("density contract is unreadable") from None
    try:
        value = json.loads(raw, object_pairs_hook=unique_keys)
    except (ConfigError, json.JSONDecodeError):
        raise DensityContractError("density contract is malformed JSON") from None
    story_text = _bounded_source(STORY_SOURCE, "upstream story")
    shell_text = _bounded_source(SHELL_SOURCE, "upstream wrapper")
    frozen_ranges, frozen_revision = _frozen_indexes()
    story_lines = story_text.splitlines()

    _exact_keys(value, _TOP_KEYS, "density contract")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise DensityContractError("density contract schema_version is unsupported")
    if value["contract"] != DENSITY_CONTRACT_ID:
        raise DensityContractError("density contract id changed")
    if value["density_prefix"] != DENSITY_PREFIX:
        raise DensityContractError("density prefix changed")
    if value["parent"] != PARENT or value["source_case_id"] != CASE_ID:
        raise DensityContractError("density contract case identity changed")
    if value["suite"] != SUITE:
        raise DensityContractError("density contract suite changed")
    if type(value["story_checks"]) is not int or value["story_checks"] != DENSITY_LEAVES:
        raise DensityContractError("density contract denominator changed")
    if value["source_revision"] != frozen_revision:
        raise DensityContractError("density source revision differs from the frozen contract")

    _validate_sources(value["sources"], value["sources_relative"])
    _validate_wrapper(value["wrapper"], shell_text)
    _validate_accounting(value["accounting"])
    _validate_local_block(value["local"])
    _validate_preprod_block(value["preprod"])
    _validate_roster(value["roster"])
    rules = value["projection_rules"]
    if not isinstance(rules, list) or len(rules) != 5:
        raise DensityContractError("density projection rules changed")
    if not all(isinstance(rule, str) and rule for rule in rules):
        raise DensityContractError("density projection rules are malformed")

    parent_projection = value["parent_projection"]
    if not isinstance(parent_projection, list) or len(parent_projection) != DENSITY_FAMILIES:
        raise DensityContractError("density parent projection must carry 15 rows")
    for row in parent_projection:
        _exact_keys(row, frozenset({"family", "leaf_count", "parent", "story_lines"}),
                    "density parent projection row")
        if row["parent"] != "PROVIDER-SUBBOTS." + row["family"]:
            raise DensityContractError(
                "density parent projection row id differs from its family")
        if row["leaf_count"] != DENSITY_FAMILY_COUNTS[row["family"]]:
            raise DensityContractError("density parent projection leaf count drifted")
        low, high = _two_int_lines(row["story_lines"], "density parent projection row")
        if [low, high] != frozen_ranges[row["parent"]]:
            raise DensityContractError(
                "density parent range differs from the frozen contract")
    if [row["family"] for row in parent_projection] != [
            name.split(".", 1)[1] for name in REQUIREMENTS]:
        raise DensityContractError("density parent projection order differs from the frozen map")

    segments, vocabulary = _evidence_vocabulary()
    for literals in ANCHORS.values():
        for literal in literals:
            if literal not in story_text:
                raise DensityContractError("frozen anchor literal left the upstream story")
    mirror = _bounded_source(MIRROR_STORY, "owned mirror story")
    if "MARKER_PREFIX + str(story.checks) + MARKER_SUFFIX" not in mirror:
        raise DensityContractError("owned mirror no longer emits the collective marker")

    obligations = value["obligations"]
    if not isinstance(obligations, list) or len(obligations) != DENSITY_LEAVES:
        raise DensityContractError("density obligations must carry exactly 131 leaves")
    _validate_leaves(obligations, story_text, frozen_ranges, segments, vocabulary)
    return value


def _validate_sources(sources: object, relative: object) -> None:
    _exact_keys(sources, frozenset(_SOURCE_NAMES), "density sources")
    _exact_keys(relative, frozenset({"product_doc", "story", "wrapper"}),
                "density sources_relative")
    if set(relative.values()) != {DOC_RELATIVE, STORY_RELATIVE, SHELL_RELATIVE}:
        raise DensityContractError("density source relative path set changed")
    relative_to_name = {DOC_RELATIVE: "agentpass-subbots.md",
                        STORY_RELATIVE: "agentpass_subbots_story.py",
                        SHELL_RELATIVE: "e2e.sh"}
    for name, digest in sources.items():
        if not isinstance(digest, str) or _HEX64.fullmatch(digest) is None:
            raise DensityContractError("density source digest is malformed: " + name)
        relative_key = RELATIVE_KEYS[name]
        declared = relative[relative_key]
        absolute = _ABSOLUTE_SOURCES[name]
        if relative_to_name.get(declared) != name:
            raise DensityContractError("density source relative binding changed: " + name)
        if REPO_ROOT / declared != absolute:
            raise DensityContractError("density source path binding changed: " + name)
        if _sha256_text(_bounded_source(absolute, "density source " + name)) != digest:
            raise DensityContractError("density pinned source digest drifted: " + name)


def _validate_wrapper(wrapper: object, shell_text: str) -> None:
    _exact_keys(wrapper, frozenset({
        "source", "function", "lines", "child", "marker_assertion", "parsed_wrappers",
        "note"}), "density wrapper")
    if wrapper["source"] != SHELL_RELATIVE or wrapper["child"] != STORY_RELATIVE:
        raise DensityContractError("density wrapper source binding changed")
    if wrapper["function"] != WRAPPER_FUNCTION or wrapper["parsed_wrappers"] != 1:
        raise DensityContractError("density wrapper function declaration changed")
    if wrapper["lines"] != [WRAPPER_LINES[0], WRAPPER_LINES[1]]:
        raise DensityContractError("density wrapper line span changed")
    if shell_text.count(WRAPPER_FUNCTION + "() {") != 1:
        raise DensityContractError("wrapper function is not unique in e2e.sh")
    low, high = WRAPPER_LINES
    wrapper_slice = _slice(shell_text, low, high)
    if not wrapper_slice.startswith(WRAPPER_FUNCTION + "() {"):
        raise DensityContractError("wrapper slice no longer starts at its function definition")
    if (WRAPPER_CHILD not in wrapper_slice
            or wrapper["marker_assertion"] != WRAPPER_MARKER
            or WRAPPER_MARKER not in wrapper_slice):
        raise DensityContractError("wrapper slice drifted from the frozen roster shape")


def _validate_accounting(accounting: object) -> None:
    _exact_keys(accounting, frozenset({
        "isomorphic", "request_status_checks", "self_view_checks", "self_view_executions",
        "self_view_success_executions", "self_view_rejection_executions", "semantic_checks",
        "story_checks_frozen", "total", "self_view_breakdown"}), "density accounting")
    if accounting["isomorphic"] is not True:
        raise DensityContractError("density execution isomorphism flag is not true")
    flags = (
        ("request_status_checks", 47), ("self_view_checks", 75),
        ("self_view_executions", 15), ("self_view_success_executions", 9),
        ("self_view_rejection_executions", 6), ("semantic_checks", 9),
        ("story_checks_frozen", 131), ("total", 131),
    )
    for key, expected in flags:
        if type(accounting[key]) is not int or accounting[key] != expected:
            raise DensityContractError("density accounting drifted: " + key)
    breakdown = accounting["self_view_breakdown"]
    _exact_keys(breakdown, frozenset({"status", "cache_control", "projection"}),
                "density self_view_breakdown")
    if breakdown != {"status": 15, "cache_control": 15, "projection": 45}:
        raise DensityContractError("density self-view projection accounting drifted")


def _validate_local_block(local: object) -> None:
    _exact_keys(local, frozenset({"derivation", "executor", "pass_rule"}), "density local")
    if local["derivation"] != LOCAL_DERIVATION or local["executor"] != LOCAL_EXECUTOR:
        raise DensityContractError("density local projection rows changed")
    if not isinstance(local["pass_rule"], str) or not local["pass_rule"]:
        raise DensityContractError("density local pass rule is malformed")


def _validate_preprod_block(preprod: object) -> None:
    _exact_keys(preprod, frozenset({
        "documented_gaps", "gap_refinement_constants", "grants_release_credit",
        "leaf_counts_by_gap_refinement", "network_requests", "remote_validation",
        "status"}), "density preprod")
    if preprod["status"] != STATUS_BLOCKED_INVENTORIED:
        raise DensityContractError("density preprod status is not BLOCKED_INVENTORIED")
    if preprod["remote_validation"] != STATUS_NOT_RUN:
        raise DensityContractError("density preprod remote_validation is not NOT_RUN")
    if preprod["network_requests"] != 0 or preprod["grants_release_credit"] is not False:
        raise DensityContractError("density preprod receipt is not honest")
    gaps = preprod["documented_gaps"]
    _exact_keys(gaps, frozenset({GAP_CONTROL_PLANE, GAP_SIGNING_KEY, GAP_ADMIN_CHANNEL}),
                "density documented gaps")
    for name, text in gaps.items():
        if not isinstance(text, str) or not 1 <= len(text) <= 2048:
            raise DensityContractError("density documented gap text is malformed: " + name)
    if "AVERNET_SECRET_PRINCIPAL_SIGNING_KEY_VALUE" not in gaps[GAP_SIGNING_KEY]:
        raise DensityContractError("signing-key gap no longer names the credential channel")
    if "provider_admin_token" not in gaps[GAP_ADMIN_CHANNEL]:
        raise DensityContractError("admin-channel gap no longer names the minted credential")
    if "NOT_RUN" not in gaps[GAP_CONTROL_PLANE]:
        raise DensityContractError("control-plane gap no longer declares NOT_RUN")
    constants = preprod["gap_refinement_constants"]
    if constants != [GAP_SIGNING_KEY, GAP_ADMIN_CHANNEL]:
        raise DensityContractError("density gap refinement constants changed")
    counts = preprod["leaf_counts_by_gap_refinement"]
    if counts != {GAP_ADMIN_CHANNEL: 15, GAP_SIGNING_KEY: 4}:
        raise DensityContractError("density gap refinement leaf counts drifted")


def _validate_roster(roster: object) -> None:
    _exact_keys(roster, frozenset({
        "density_delta", "density_leaves", "density_rule", "embedded_live_checks",
        "local_obligations_before", "upstream_parsed_wrappers"}), "density roster")
    if (roster["embedded_live_checks"] != 131 or roster["local_obligations_before"] != 15
            or roster["density_leaves"] != 131 or roster["density_delta"] != 116
            or roster["upstream_parsed_wrappers"] != 1):
        raise DensityContractError("density roster accounting drifted")
    if not isinstance(roster["density_rule"], str) or not roster["density_rule"]:
        raise DensityContractError("density roster rule is malformed")


def _validate_leaves(obligations: list, story_text: str, frozen_ranges: dict,
                     segments: frozenset, vocabulary: frozenset) -> None:
    story_lines = story_text.splitlines()
    kinds: dict[str, int] = {}
    families: dict[str, int] = {}
    bases: dict[str, int] = {}
    refinements: dict[str, int] = {}
    numbering: dict[str, int] = {}
    identifiers: set[str] = set()
    checkpoints: dict[int, int] = {}
    view_success = 0
    view_rejection = 0
    ordinal_rows: set[tuple[int, object, int, str]] = set()
    ordinal_status: dict[int, int] = {}
    for index, leaf in enumerate(obligations):
        where = "density leaf " + str(index + 1)
        _exact_keys(leaf, _LEAF_KEYS, where)
        if type(leaf["check_no"]) is not int or leaf["check_no"] != index + 1:
            raise DensityContractError(where + ": check_no is not the emission order")
        if not isinstance(leaf["id"], str) or _ID_PATTERN.fullmatch(leaf["id"]) is None:
            raise DensityContractError(where + ": leaf id is malformed")
        if leaf["id"] in identifiers:
            raise DensityContractError(where + ": leaf id is not unique")
        identifiers.add(leaf["id"])
        family = leaf["family"]
        if family not in DENSITY_FAMILY_COUNTS:
            raise DensityContractError(where + ": unknown density family")
        if leaf["parent"] != "PROVIDER-SUBBOTS." + family:
            raise DensityContractError(where + ": parent differs from its family")
        seen = numbering.get(family, 0) + 1
        if leaf["id"] != DENSITY_PREFIX + "." + family + "." + str(seen).zfill(3):
            raise DensityContractError(where + ": family numbering is not emission order")
        numbering[family] = seen
        families[family] = families.get(family, 0) + 1
        for key in ("title", "expectation", "upstream_label"):
            if not isinstance(leaf[key], str) or not leaf[key]:
                raise DensityContractError(where + ": " + key + " is malformed")
        if leaf["expectation"] != leaf["title"]:
            raise DensityContractError(where + ": expectation diverged from its title")
        anchor = _exact_keys(leaf["anchor"], _ANCHOR_KEYS, where + ".anchor")
        _exact_keys(leaf["projection"], frozenset({"basis", "story_lines"}),
                    where + ".projection")
        evidence = _exact_keys(leaf["evidence"], _EVIDENCE_KEYS, where + ".evidence")
        local_row = _exact_keys(leaf["local"],
                                frozenset({"executable", "executor", "derivation"}),
                                where + ".local")
        _exact_keys(leaf["preprod"], frozenset(
            {"gap", "gap_refinements", "network_requests", "status"}), where + ".preprod")
        kind = leaf["kind"]
        kinds[kind] = kinds.get(kind, 0) + 1
        basis = leaf["projection"]["basis"]
        bases[basis] = bases.get(basis, 0) + 1
        _validate_leaf_anchor(anchor, leaf, story_lines, story_text, ordinal_rows,
                              ordinal_status, where)
        _validate_leaf_evidence(evidence, leaf, frozen_ranges, segments, vocabulary, where)
        if local_row != {"executable": True, "executor": LOCAL_EXECUTOR,
                         "derivation": LOCAL_DERIVATION}:
            raise DensityContractError(where + ": local projection row is invalid")
        preprod_row = leaf["preprod"]
        if (preprod_row["status"] != STATUS_NOT_RUN
                or preprod_row["network_requests"] != 0
                or preprod_row["gap"] != GAP_CONTROL_PLANE):
            raise DensityContractError(where + ": preprod receipt is not honest")
        expected_refinements = []
        if anchor["site_line"] in SIGNED_PRINCIPAL_LINES:
            expected_refinements.append(GAP_SIGNING_KEY)
        if anchor["site_line"] in ADMIN_CHANNEL_LINES:
            expected_refinements.append(GAP_ADMIN_CHANNEL)
        if preprod_row["gap_refinements"] != expected_refinements:
            raise DensityContractError(
                where + ": gap refinement membership drifted from the pinned lines")
        for gap in preprod_row["gap_refinements"]:
            refinements[gap] = refinements.get(gap, 0) + 1
        checkpoints.setdefault(anchor["site_line"], leaf["check_no"])
        if kind == "self-view":
            if leaf["expected_status"] == 200:
                view_success += 1
            elif leaf["expected_status"] == 401:
                view_rejection += 1
            else:
                raise DensityContractError(where + ": self-view expected status drifted")
    if kinds != DENSITY_KIND_COUNTS:
        raise DensityContractError("density kind accounting drifted")
    if families != DENSITY_FAMILY_COUNTS:
        raise DensityContractError("density family counts drifted")
    if set(families) != {name.split(".", 1)[1] for name in REQUIREMENTS}:
        raise DensityContractError("density projection is not surjective onto the 15 parents")
    if bases != DENSITY_BASIS_COUNTS:
        raise DensityContractError("density projection basis accounting drifted")
    if refinements != DENSITY_REFINEMENT_COUNTS:
        raise DensityContractError("density gap refinement accounting drifted")
    if view_success != 63 or view_rejection != 12:
        raise DensityContractError("self-view success/rejection accounting drifted")
    for line, expected in ((145, 1), (204, 13), (274, 25), (408, 91), (476, 131)):
        if checkpoints.get(line) != expected:
            raise DensityContractError(
                "execution-order checkpoint drifted at upstream L" + str(line))
    for ordinal, status in ordinal_status.items():
        expected_count = 7 if status == 200 else 2
        rows = sum(1 for row in ordinal_rows if row[2] == ordinal)
        if rows != expected_count:
            raise DensityContractError("self-view ordinal accounting drifted: " + str(ordinal))


def _validate_leaf_anchor(anchor: dict, leaf: dict, story_lines: list[str],
                          story_text: str, ordinal_rows: set, ordinal_status: dict,
                          where: str) -> None:
    if anchor["file"] != STORY_RELATIVE:
        raise DensityContractError(where + ": anchor file drifted from the upstream story")
    site_line = anchor["site_line"]
    site_end = anchor["site_end_line"]
    if (type(site_line) is not int or type(site_end) is not int
            or not 1 <= site_line <= site_end <= len(story_lines)):
        raise DensityContractError(where + ": anchor site span is invalid")
    if anchor["site_slice"] != _slice(story_text, site_line, site_end):
        raise DensityContractError(where + ": anchor site slice drifted from the upstream story")
    if (not isinstance(anchor["label_literal"], str)
            or anchor["label_literal"] not in story_text):
        raise DensityContractError(where + ": anchor label literal left the upstream story")
    multiplicity = STATIC_MULTIPLICITY.get(site_line, 1)
    if anchor["multiplicity"] != multiplicity:
        raise DensityContractError(where + ": static multiplicity drifted")
    loop_index = anchor["loop_index"]
    loop_values = anchor["loop_values"]
    if multiplicity == 1:
        if loop_index is not None or loop_values is not None:
            raise DensityContractError(where + ": non-loop leaf carries loop state")
    else:
        if type(loop_index) is not int or not 1 <= loop_index <= multiplicity:
            raise DensityContractError(where + ": loop index is outside its frozen tuple")
        if (not isinstance(loop_values, list) or not loop_values
                or any(not isinstance(item, (str, list)) for item in loop_values)):
            raise DensityContractError(where + ": loop values drifted from the frozen tuple")
    kind = leaf["kind"]
    expansion = anchor["expansion"]
    if kind == "self-view":
        if leaf["method"] != "GET" or leaf["path"] != VIEW_PATH:
            raise DensityContractError(where + ": self-view route drifted")
        ordinal = anchor["view_execution"]
        if type(ordinal) is not int or not 1 <= ordinal <= 15:
            raise DensityContractError(where + ": self-view execution ordinal is invalid")
        if anchor["view_bot"] not in {"a", "b"}:
            raise DensityContractError(where + ": self-view bot identity is invalid")
        if anchor["view_actor_kind"] not in DENSITY_VIEW_ACTOR_KINDS:
            raise DensityContractError(where + ": self-view actor kind is invalid")
        if not isinstance(anchor["view_context"], str) or not anchor["view_context"]:
            raise DensityContractError(where + ": self-view context is malformed")
        has_loop = loop_index is not None and loop_values is not None
        if (site_line in VIEW_GROUP_LINES) != has_loop:
            raise DensityContractError(where + ": self-view loop state is inconsistent")
        if expansion is None or expansion != leaf["subkind"]:
            raise DensityContractError(where + ": self-view expansion diverged from its subkind")
        valid = (VIEW_SUCCESS_EXPANSIONS if leaf["expected_status"] == 200
                 else VIEW_REJECTION_EXPANSIONS)
        if expansion not in valid:
            raise DensityContractError(where + ": self-view expansion left its validator fields")
        row = (site_line, loop_index, ordinal, expansion)
        if row in ordinal_rows:
            raise DensityContractError(where + ": self-view execution is not unique")
        ordinal_rows.add(row)
        previous = ordinal_status.get(ordinal)
        if previous is not None and previous != leaf["expected_status"]:
            raise DensityContractError(where + ": self-view ordinal changed its expected status")
        ordinal_status[ordinal] = leaf["expected_status"]
    elif kind == "request-status":
        if (not isinstance(leaf["method"], str) or leaf["method"] not in DENSITY_METHODS
                or not isinstance(leaf["path"], str) or not leaf["path"].startswith("/")):
            raise DensityContractError(where + ": request-status surface drifted")
        if expansion is not None or leaf["subkind"] is not None:
            raise DensityContractError(where + ": request-status leaf carries view state")
        for key in ("view_execution", "view_context", "view_bot", "view_actor_kind"):
            if anchor[key] is not None:
                raise DensityContractError(where + ": request leaf carries self-view state")
        if type(leaf["expected_status"]) is not int:
            raise DensityContractError(where + ": request-status expected status is malformed")
        if not isinstance(leaf["actor"], str) or not leaf["actor"]:
            raise DensityContractError(where + ": request-status actor is malformed")
        if (leaf["actor_headers"] is not None
                and (not isinstance(leaf["actor_headers"], str) or not leaf["actor_headers"])):
            raise DensityContractError(where + ": request-status actor headers are malformed")
    else:
        if (leaf["method"] is not None or leaf["path"] is not None
                or leaf["expected_status"] is not None or leaf["actor_headers"] is not None
                or leaf["actor"] != "response-payload" or leaf["subkind"] is not None):
            raise DensityContractError(where + ": semantic leaf drifted from its projection")
        if expansion is not None:
            raise DensityContractError(where + ": semantic leaf carries self-view state")
        for key in ("view_execution", "view_context", "view_bot", "view_actor_kind"):
            if anchor[key] is not None:
                raise DensityContractError(where + ": semantic leaf carries self-view state")
    if anchor["view_actor_kind"] is not None and kind != "self-view":
        raise DensityContractError(where + ": non self-view leaf carries actor kind state")


def _validate_leaf_evidence(evidence: dict, leaf: dict, frozen_ranges: dict,
                            segments: frozenset, vocabulary: frozenset,
                            where: str) -> None:
    if evidence["collective_marker"] != STORY_MARKERS[0]:
        raise DensityContractError(
            where + ": collective marker drifted from the frozen story")
    segment = evidence["primary_segment"]
    path = evidence["primary_path"]
    if segment is None:
        if (path is not None or leaf["anchor"]["site_line"] != 213
                or leaf["check_no"] != 14):
            raise DensityContractError(
                where + ": only upstream L213 may carry a null evidence primary")
    else:
        if segment not in segments:
            raise DensityContractError(where + ": evidence segment is unknown to checks.py")
        if not isinstance(path, list) or len(path) < 2 or path[0] != segment:
            raise DensityContractError(where + ": evidence primary path is malformed")
        if any(item not in vocabulary for item in path):
            raise DensityContractError(where + ": evidence path is unknown to checks.py")
    cross = evidence["cross_segment_validation"]
    if cross is not None:
        if not isinstance(cross, list) or not cross:
            raise DensityContractError(where + ": cross segment validation is malformed")
        for entry in cross:
            _exact_keys(entry, frozenset({"segment", "field"}), where + ".cross")
            if entry["segment"] not in segments or entry["field"] not in vocabulary:
                raise DensityContractError(where + ": cross segment is unknown to checks.py")
    basis = leaf["projection"]["basis"]
    low, high = _two_int_lines(leaf["projection"]["story_lines"], where + ".projection")
    if [low, high] != frozen_ranges[leaf["parent"]]:
        raise DensityContractError(where + ": projection range differs from the frozen contract")
    if basis not in DENSITY_BASIS_COUNTS:
        raise DensityContractError(where + ": unknown projection basis")
    if leaf["kind"] == "semantic" and basis != "density-semantic-declaration":
        raise DensityContractError(where + ": semantic leaf basis drifted")
    if leaf["kind"] != "semantic" and basis == "density-semantic-declaration":
        raise DensityContractError(where + ": semantic basis leaked onto an executed check")
    if basis == "documented-exception":
        if leaf["anchor"]["site_line"] != 204:
            raise DensityContractError(where + ": documented exception left upstream L204")
        if leaf["parent"] != "PROVIDER-SUBBOTS.duplicate-reference-conflicts":
            raise DensityContractError(where + ": documented exception owner changed")


def density_leaves(contract: dict | None = None) -> list:
    """Return the validated 131 density leaves."""
    return (load_density_contract() if contract is None else contract)["obligations"]


def density_families(contract: dict | None = None) -> list[str]:
    """Return the 15 frozen family names in projection order."""
    value = load_density_contract() if contract is None else contract
    return [row["family"] for row in value["parent_projection"]]


def density_parent_projection(contract: dict | None = None) -> dict[str, int]:
    """Return the parent obligation id to density leaf count map."""
    value = load_density_contract() if contract is None else contract
    return {row["parent"]: row["leaf_count"] for row in value["parent_projection"]}


def density_contract_digest() -> str:
    """Return the sha256 digest of the raw density contract bytes."""
    return hashlib.sha256(DENSITY_CONTRACT_PATH.read_bytes()).hexdigest()


def _collective_oracle(result: object) -> tuple[object, set[str] | None]:
    """Echo the real child receipt counters without resolving any credential."""
    observed: object = None
    checked_ids: set[str] | None = None
    if isinstance(result, dict):
        receipt = result.get("child_receipt")
        if isinstance(receipt, dict):
            observed = receipt.get("observed_checks")
        checks_row = result.get("checks")
        if isinstance(checks_row, dict):
            checked = checks_row.get("checked")
            if isinstance(checked, list) and all(isinstance(item, str) for item in checked):
                checked_ids = set(checked)
    return observed, checked_ids


def project_local_density(result: object) -> dict:
    """Project the owned-local density leaves from a real child receipt.

    PASS flows only through the frozen collective criterion: the child receipt
    must observe exactly 131 live checks with status PASS over all 15 frozen
    requirements.  Any partial, fused or failing receipt leaves every leaf
    honestly NOT_RUN with the named gap; a leaf is never partially passed.
    """
    contract = load_density_contract()
    observed, checked_ids = _collective_oracle(result)
    checks_row = result.get("checks") if isinstance(result, dict) else None
    passed = (
        isinstance(result, dict)
        and result.get("status") == LOCAL_STATUS
        and isinstance(checks_row, dict)
        and checks_row.get("status") == "PASS"
        and observed == DENSITY_LEAVES
        and checked_ids == set(REQUIREMENTS)
    )
    leaves = []
    for leaf in contract["obligations"]:
        row = {
            "id": leaf["id"],
            "check_no": leaf["check_no"],
            "family": leaf["family"],
            "parent": leaf["parent"],
            "status": "PASS" if passed else STATUS_NOT_RUN,
            "basis": LOCAL_DERIVATION,
        }
        if not passed:
            row["named_gap"] = LOCAL_DENSITY_GAP
        leaves.append(row)
    return {
        "status": "PASS" if passed else "BLOCKED",
        "derivation": LOCAL_DERIVATION,
        "pass_rule": contract["local"]["pass_rule"],
        "collective": {
            "status": "PASS" if passed else "BLOCKED",
            "criteria": (
                "status==LOCAL_STATUS and checks.status=='PASS' and "
                "observed_checks==131 and checked==the 15 frozen requirements"
            ),
            "observed_checks": observed,
            "checked_count": len(checked_ids) if checked_ids is not None else None,
            "checked_requirement_ids": (sorted(checked_ids) if checked_ids is not None else None),
        },
        "named_gap": None if passed else LOCAL_DENSITY_GAP,
        "counts": {"pass": DENSITY_LEAVES if passed else 0,
                   "not_run": 0 if passed else DENSITY_LEAVES},
        "leaves": leaves,
    }


def project_preprod_density() -> dict:
    """Project the honest preprod receipt: BLOCKED_INVENTORIED, never PASS."""
    contract = load_density_contract()
    declared = contract["preprod"]
    leaves = []
    for leaf in contract["obligations"]:
        leaves.append({
            "id": leaf["id"],
            "check_no": leaf["check_no"],
            "family": leaf["family"],
            "parent": leaf["parent"],
            "status": STATUS_NOT_RUN,
            "network_requests": 0,
            "gap": leaf["preprod"]["gap"],
            "gap_refinements": list(leaf["preprod"]["gap_refinements"]),
        })
    return {
        "status": declared["status"],
        "remote_validation": declared["remote_validation"],
        "network_requests": declared["network_requests"],
        "grants_release_credit": False,
        "documented_gaps": declared["documented_gaps"],
        "gap_refinement_constants": list(declared["gap_refinement_constants"]),
        "leaf_counts_by_gap_refinement": declared["leaf_counts_by_gap_refinement"],
        "counting_rule": (
            "Every density leaf stays NOT_RUN with network_requests=0 under the story-wide "
            "control-plane gap and its signing-key or admin-credential refinements; the "
            "route-surface probe never converts a density leaf to PASS."
        ),
        "counts": {"pass": 0, "not_run": DENSITY_LEAVES},
        "leaves": leaves,
    }


def build_case_density_projection(tier: str, local_result: object = None,
                                  probe: object = None) -> dict:
    """Build the envelope-embedded density projection for one environment tier.

    The preprod tier carries the probe status as metadata only; even a passing
    route-surface probe keeps every density leaf NOT_RUN and grants no release
    credit.
    """
    base = {
        "tier": tier,
        "contract": DENSITY_CONTRACT_ID,
        "density_prefix": DENSITY_PREFIX,
        "leaves_total": DENSITY_LEAVES,
    }
    if tier == "local":
        return {**base, **project_local_density(local_result)}
    if tier == "preprod":
        projection = {**base, **project_preprod_density()}
        projection["probe_status"] = probe.get("status") if isinstance(probe, dict) else None
        projection["probe_semantics"] = (
            "The route-surface probe is metadata only: even a passing probe never "
            "converts a density leaf to PASS, and the full 131-check story stays "
            "NOT_RUN with network_requests=0."
        )
        return projection
    raise DensityContractError("density projection requires the local or preprod tier")
