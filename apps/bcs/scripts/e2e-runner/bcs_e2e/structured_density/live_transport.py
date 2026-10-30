"""Owned-local live transport bridging the structured density journey.

The 43-point density roster is a single 17-request journey over the reviewed
legacy structured-collaboration routes.  Preprod evidence collected 2026-10-31
fixes the wire topology: the 15 structured Group/definition/run obligations
execute on the station-direct ``bcs-direct`` face (r8 journey shape, Human
Cookie throughout, station-internal ACE), while the two catalog observations
execute on the Gateway public-v1 face with an explicit browser origin — the
station front door does not serve the catalog slice (HTTP 401 without the
Gateway browser context; probe evidence ``templates-face-probe``), and the
Gateway does not deploy the structured Group create route (HTTP 404; probe
evidence ``gateway-structured-core-20261031``).  Singlebox keeps one origin
for both families, so both clients share a single profile there.

Journey semantics mirror the reviewed core adapter: mutations execute at
most once, reads replay at their journey step (``run.read`` is a distinct
live request before the Human-input inspection and again after cancellation),
and every later request for an already-executed operation returns the
recorded observation so the 26 field rows stay deterministic memo hits.
No credential value ever leaves this module: only bounded projections,
status ints and transport records flow back.
"""
from __future__ import annotations

from typing import Any

from ..local_structured_core_contract import (
    BOT_UUID_PLACEHOLDER,
    CANCEL_BODY,
    DEFINITION_YAML,
    GROUP_LABEL,
    HUMAN_RESPONSE_BODY,
    ORIGINAL_INSTRUCTION,
    PATCHED_INSTRUCTION,
    RUN_INPUT,
)
from ..run_state import RunStateError
from ..transport import ReadClient
from ..write_transport import WriteClient
from .transport import Observation

READ = "read"
WRITE = "write"

# (density operation, leg, legacy route operation, expected HTTP status(es),
#  explicit negative-admission).  The tuple order IS the journey order.
JOURNEY_STEPS: tuple[tuple[str, str, str, Any, bool], ...] = (
    ("templates.list", READ, "structured.templates.list", 200, False),
    ("templates.detail", READ, "structured.template.detail", 200, False),
    ("group.create", WRITE, "structured.group.create", 200, False),
    ("group.read", READ, "structured.group.read", 200, False),
    ("definition.read", READ, "structured.definition.read", 200, False),
    ("definition.patch", WRITE, "structured.definition.patch", 200, False),
    ("definition.upgrade", WRITE, "structured.definition.upgrade.missing", 404, True),
    ("run.start", WRITE, "structured.run.start", 202, False),
    ("run.read", READ, "structured.run.read", 200, False),
    ("run.graph", READ, "structured.run.graph.read", 200, False),
    ("run.node", READ, "structured.run.node.read", 200, False),
    ("run.pending-human", READ, "structured.run.pending.human.nodes", 200, False),
    ("run.human-response", WRITE, "structured.run.human.response.denied", (400, 409), True),
    ("run.cancel", WRITE, "structured.run.cancel", (200, 409), True),
    ("run.terminal", READ, "structured.run.read", 200, False),
    ("run.rerun", WRITE, "structured.run.rerun.aborted.denied", 409, True),
    ("group.delete", WRITE, "structured.group.delete", 200, False),
)

STEP_INDEX = {step[0]: index for index, step in enumerate(JOURNEY_STEPS)}
STRUCTURED_MUTATIONS = frozenset(
    step[0] for step in JOURNEY_STEPS if step[1] == WRITE)


class LiveStructuredDensityTransport:
    """Execute the 17-step structured density journey on admitted profiles.

    ``structured_profile`` carries the station-direct structured-collaboration
    leg; ``templates_profile`` carries the Gateway catalog leg (the same
    profile object for an owned-local Singlebox origin).  Both are admitted by
    the slice profile validators before this transport is constructed.
    """

    def __init__(self, structured_profile: dict, templates_profile: dict):
        if not isinstance(structured_profile, dict) or not isinstance(templates_profile, dict):
            raise RunStateError("structured density transport requires two admitted profiles")
        self.structured_profile = structured_profile
        self.templates_profile = templates_profile
        self.read_client = ReadClient(structured_profile)
        self.write_client = WriteClient(structured_profile)
        same_origin = templates_profile is structured_profile
        self.catalog_client = self.read_client if same_origin else ReadClient(templates_profile)
        self._memo: dict[str, Observation] = {}
        self._attempted: list[str] = []
        self._group_id = ""
        self._definition_id = ""
        self._base_version = 0
        self._patched_version = 0
        self._run_id = ""

    # --- journey state queries (used by the case-run cleanup contract) ---

    def bound_group_id(self) -> str:
        return self._group_id

    def group_delete_executed(self) -> bool:
        return "group.delete" in self._memo

    def journey_log(self) -> list[dict[str, Any]]:
        """Bounded evidence of the executed journey steps (no credentials)."""
        legacy_by_op = {step[0]: step[2] for step in JOURNEY_STEPS}
        return [
            {"operation": operation,
             "legacy_operation": legacy_by_op.get(operation, operation),
             "http_status": self._memo[operation].status,
             "mutation": operation in STRUCTURED_MUTATIONS}
            for operation in self._attempted
        ]

    # --- transport protocol surface used by the density projector ---

    def request(self, operation: str, assertion_id: str) -> Observation:
        """Return the deterministic observation for one assertion site."""
        if operation in self._memo:
            return self._memo[operation]
        step = STEP_INDEX.get(operation)
        if step is None:
            raise RunStateError(f"unsupported structured density operation: {operation}")
        if step != len(self._attempted):
            raise RunStateError(
                "structured density journey requested out of order: "
                f"{operation} is step {step + 1} of 17")
        observation = self._execute(operation)
        self._attempted.append(operation)
        self._memo[operation] = observation
        return observation

    def bind_context(self, context: dict[str, Any], operation: str,
                     observation: Observation) -> None:
        """Bind live identifiers into the projector's assertion context.

        Only non-empty string/int values are bound; offline replay fixtures
        that lack an identity key keep their frozen placeholder.  ``group
        identity`` binds at creation, the definition identity and base version
        at the definition read (the missing upgrade target is derived as
        base+100 at that same moment), and the Run identity at start.  The
        patch response version intentionally does not rebind the projected
        expectation, which stays anchored to the frozen base-plus-one rule.
        """
        body = observation.body
        if not isinstance(body, dict):
            return
        if operation == "group.create":
            value = body.get("id")
            if isinstance(value, str) and value:
                context["group_id"] = value
        elif operation == "definition.read":
            definition = body.get("default_definition")
            if isinstance(definition, dict):
                value = definition.get("id")
                if isinstance(value, str) and value:
                    context["definition_id"] = value
                version = definition.get("version")
                if type(version) is int:
                    context["base_version"] = version
                    context["missing_version"] = version + 100
        elif operation == "run.start":
            run = body.get("run")
            if isinstance(run, dict):
                value = run.get("run_id")
                if isinstance(value, str) and value:
                    context["run_id"] = value

    # --- journey internals ---

    def _require_group(self) -> str:
        if not self._group_id:
            raise RunStateError(
                "structured density group creation must precede group-scoped obligations")
        return self._group_id

    def _require_run(self) -> str:
        if not self._run_id:
            raise RunStateError(
                "structured density run start must precede run-scoped obligations")
        return self._run_id

    def _execute(self, operation: str) -> Observation:
        if operation == "templates.list":
            return self._catalog_observation("structured.templates.list")
        if operation == "templates.detail":
            return self._catalog_observation("structured.template.detail")

        step = JOURNEY_STEPS[STEP_INDEX[operation]]
        kind, legacy, expected = step[1], step[2], step[3]
        expected_status = (expected,) if isinstance(expected, int) else expected

        if kind == READ:
            return self._structured_read(operation, legacy)

        payload, record = self.write_client.structured_call(
            legacy, group_id=self._group_id, run_id=self._run_id,
            body=self._write_body(operation), expected=expected_status,
            parse_error=step[4])
        self._bind_journey(operation, payload)
        return Observation(record["http_status"], payload)

    def _catalog_observation(self, legacy_operation: str) -> Observation:
        observation = self.catalog_client.read(legacy_operation)
        return Observation(observation.evidence["http_status"], observation.data)

    def _structured_read(self, operation: str, legacy_operation: str) -> Observation:
        if operation in {"group.read", "definition.read"}:
            observation = self.read_client.read(
                legacy_operation, group_id=self._require_group())
        else:
            self._require_run()
            observation = self.read_client.read(legacy_operation, run_id=self._run_id)
        data = observation.data
        if operation == "run.pending-human":
            items = data.get("items") if isinstance(data, dict) else None
            if not isinstance(items, list) or items:
                raise RunStateError(
                    "structured density pending-Human projection must be an empty array")
            data = items
        self._bind_journey(operation, data)
        return Observation(observation.evidence["http_status"], data)

    def _write_body(self, operation: str) -> dict | None:
        if operation == "group.create":
            return self._group_create_body()
        if operation == "definition.patch":
            return self._definition_patch_body()
        if operation == "definition.upgrade":
            return self._definition_upgrade_body()
        if operation == "run.start":
            return dict(RUN_INPUT)
        if operation == "run.human-response":
            return dict(HUMAN_RESPONSE_BODY)
        if operation == "run.cancel":
            return dict(CANCEL_BODY)
        return None

    def _actor_ids(self) -> tuple[str, str]:
        actors = self.structured_profile.get("actors", {})
        driver = actors.get("driver", {}).get("bot_id", "")
        member = actors.get("member", {}).get("bot_id", "")
        if not isinstance(driver, str) or not driver:
            raise RunStateError("structured density profile lacks the driver Bot identity")
        if not isinstance(member, str) or not member:
            raise RunStateError("structured density profile lacks the member Bot identity")
        return driver, member

    def _group_create_body(self) -> dict:
        driver, member = self._actor_ids()
        body: dict[str, Any] = {
            "driver_bot": driver,
            "label": GROUP_LABEL,
            "participants": [{"bot_uuid": driver}, {"bot_uuid": member}],
            "group_strategy": "state_machine",
            "collaboration_definition_yaml": DEFINITION_YAML.replace(BOT_UUID_PLACEHOLDER, driver),
        }
        if self.structured_profile.get("entrypoint", {}).get("kind") == "bcs-direct":
            # The reviewed definition declares only the driver slot; the member
            # stays an explicit Group participant but never an undeclared
            # state-machine binding slot on the station-direct face.
            body["participant_bindings"] = {
                "driver": {"source": "manual", "bot_ids": [driver]},
            }
        return body

    def _definition_patch_body(self) -> dict:
        if not self._definition_id:
            raise RunStateError("structured density definition read must precede the patch")
        return {
            "base_definition": {"id": self._definition_id,
                                "version": self._base_version},
            "definition_yaml": DEFINITION_YAML.replace(ORIGINAL_INSTRUCTION, PATCHED_INSTRUCTION),
        }

    def _definition_upgrade_body(self) -> dict:
        if not self._definition_id:
            raise RunStateError("structured density definition read must precede the upgrade")
        return {
            "base_definition": {"id": self._definition_id,
                                "version": self._patched_version or self._base_version},
            "target_definition": {"id": self._definition_id,
                                  "version": self._base_version + 100},
        }

    def _bind_journey(self, operation: str, body: Any) -> None:
        if not isinstance(body, dict):
            return
        if operation == "group.create":
            value = body.get("id")
            if not isinstance(value, str) or not value:
                raise RunStateError(
                    "structured density group creation did not return a Group ID")
            self._group_id = value
        elif operation == "group.read":
            if body.get("group_strategy") != "state_machine":
                raise RunStateError(
                    "structured density group read does not record the state-machine strategy")
        elif operation == "definition.read":
            definition = body.get("default_definition")
            if not isinstance(definition, dict):
                raise RunStateError(
                    "structured density definition read lacks the bound definition")
            value = definition.get("id")
            version = definition.get("version")
            if not isinstance(value, str) or not value:
                raise RunStateError(
                    "structured density bound definition lacks its identity")
            if type(version) is not int or version < 1:
                raise RunStateError(
                    "structured density bound definition version is invalid")
            self._definition_id = value
            self._base_version = version
        elif operation == "definition.patch":
            definition = body.get("default_definition")
            if not isinstance(definition, dict):
                raise RunStateError(
                    "structured density definition patch response is invalid")
            version = definition.get("version")
            if type(version) is not int or version != self._base_version + 1:
                raise RunStateError(
                    "structured density definition patch did not increment the version by one")
            self._patched_version = version
        elif operation == "run.start":
            run = body.get("run")
            nodes = body.get("nodes")
            if not isinstance(run, dict):
                raise RunStateError("structured density run start lacks the Run")
            value = run.get("run_id")
            if not isinstance(value, str) or not value:
                raise RunStateError("structured density run start lacks the Run ID")
            if run.get("group_id") != self._group_id:
                raise RunStateError("structured density run belongs to another Group")
            if not isinstance(nodes, list) or not nodes or not isinstance(nodes[0], dict):
                raise RunStateError("structured density run start lacks the node state")
            self._run_id = value
