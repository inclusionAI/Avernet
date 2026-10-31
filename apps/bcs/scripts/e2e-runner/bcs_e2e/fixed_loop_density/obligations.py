"""Source-anchored leaf roster for the two dynamic fixed-Loop stories (E2a).

This module projects the frozen executor ledgers (66 fixed + 46 nested python
sites) onto a chargeable obligation roster without re-declaring any migrated
requirement:

* the 8 fixed and 16 nested migration-contract parent envelopes are reused
  verbatim from the frozen JSON contracts through the density contract;
* every python assert site becomes exactly one charged leaf (FLOOP.F02-F34,
  NFLOOP.F02-NF25) carrying a comparator, a field, a frozen expected value and
  a disk-assembled quote;
* the wrapper shell facade charges as one leaf per story (FLOOP.F01,
  NFLOOP.F01) under the disclosed face-derived auxiliary gate envelope
  (FIXED_LOOP.story-gate / NESTED-LOOP.story-gate, uncharged);
* every python transport site rides its owning migration-contract envelope(s)
  as a parent-internal checkpoint; the owned-local seam table (symbolic
  method, path, expected/accepted statuses, mock/carrier/CLI plane and note)
  is frozen here and cross-checked against the story source bytes at build
  time so a story edit can never silently re-anchor a checkpoint;
* the nested child inherits the base Story finish_bot assert family and the
  cleanup stations as a NON-additive cross-family annex disclosed on the plan;
* conditional branches charge once with explicit dormancy and leg
  disclosures (approved-only / exhausted-only fixed arms, guarded nested
  iterations and completion branches);
* nothing is normalized to PASS, no transport opens here, and no credential
  value appears in this source (symbolic identities only).

Charged-row caliber: 42 fixed rows (8 parents + 34 leaves) and 41 nested rows
(16 parents + 25 leaves); site caliber: 67/47 parsed sites (leaves plus
parent-internal transport checkpoints plus one shell facade per story); both
calibers carry residual_count_gap 0 and are never merged.
"""
from __future__ import annotations

from typing import Any

from ..dynamic_family.contract import dynamic_family_contract
from ..run_state import RunStateError
from .catalog import (
    B_UPSTREAM_NOT_ROSTERED, FACE_WRAPPER_GATE, FAMILY_FIXED, FAMILY_NESTED,
    FIXED_ASSERT_SITE_COUNT, FIXED_CHARGED_ROW_COUNT, FIXED_CONTRACT_ENVELOPE,
    FIXED_CONTRACT_FILE, FIXED_LEAF_COUNT, FIXED_LEDGER_SITE_COUNT, FIXED_PARENT_COUNT,
    FIXED_PREFIX, FIXED_PY_FILE, FIXED_SH_ASSERT_LINE, FIXED_SH_FILE, FIXED_SH_STORY_SPAN,
    FIXED_TRANSPORT_SITE_COUNT, FIXED_WRAPPER_LEAF, GATE_ID_FIXED, GATE_ID_NESTED,
    NESTED_ASSERT_SITE_COUNT, NESTED_CHARGED_ROW_COUNT, NESTED_CONTRACT_ENVELOPE,
    NESTED_CONTRACT_FILE, NESTED_LEAF_COUNT, NESTED_LEDGER_SITE_COUNT, NESTED_PARENT_COUNT,
    NESTED_PREFIX, NESTED_PY_FILE, NESTED_SH_ASSERT_LINE, NESTED_SH_STORY_SPAN,
    NESTED_TRANSPORT_SITE_COUNT, NESTED_WRAPPER_LEAF, Obligation, STATUS_PASS, STORY_FIXED,
    STORY_NESTED, SYMBOL_ARTIFACT, SYMBOL_CREDENTIAL_DOWNLINK, SYMBOL_DELIVERY_EARLY,
    SYMBOL_EXECUTION_NODE_FIRST_INNER, SYMBOL_GROUP_ECHO, SYMBOL_HUMAN_OUTPUT_FIRST_INNER,
    SYMBOL_NODE_SECOND, SYMBOL_REF_FIRST, SYMBOL_REF_SECOND, SYMBOL_SESSION_ECHO,
    UPSTREAM_NOT_ROSTERED, site_records, site_span_index, story_lines,
    wrapper_sh_story_lines
)
from .contract import (
    FIXED_PREPROD_GAP_ID, NESTED_PREPROD_GAP_ID, build_fixed_parent_envelopes,
    build_nested_parent_envelopes, wrapper_gate_envelope
)
from .registrations import (
    DUAL_FACE_DIVERGENCE_ID, FACE_UNDERCOUNT_ID, FIXED_PREPROD_GAP_ID,
    NESTED_PREPROD_GAP_ID,
)

# Envelope provenance for the two leaf kinds.
LEDGER_LEAF_ENVELOPE_SOURCE = "executor_ledger_assert_site"
FACADE_LEAF_CLASSIFICATION = "shell_facade_atom"
LEAF_CLASSIFICATION = "assertion_atom"
PARENT_CLASSIFICATION = "migration_contract_requirement_row"
GATE_CLASSIFICATION = "face_wrapper_gate"

KIND_ASSERT = "assert"
KIND_TRANSPORT = "transport"

# Fixed story arm legs (frozen from the story source guards): every site rides
# both arms except the three single-arm start stations and the three
# single-arm verify/posture asserts.
FIXED_DEFAULT_LEGS = ("approved", "exhausted")
FIXED_ARM_LEG_RULES = {
    183: ("approved",),   # state-machine-run start lives in the approved arm
    185: ("exhausted",),  # one-shot Session creation lives in the exhausted arm
    187: ("exhausted",),  # one-shot CLI Run admission lives in the exhausted arm
    254: ("approved",),   # unreached third-iteration nodes exist only on the approved loop
    285: ("exhausted",),  # surviving Chat session posture is an exhausted-arm property
    289: ("exhausted",),  # exactly-once chat publication is asserted on the exhausted arm
}
NESTED_MARKER_LEGS = ("nested-bot-story", "nested-human-story")

# Runtime-conditional charged leaves (dormant unless their guard runs); they
# charge exactly once with the dormancy basis, never silently.
FIXED_CONDITIONAL_LEAVES = {
    "FLOOP.F21": "runtime guard 'if approved:' - the unreached third-iteration skip set exists only on the approved arm; the exhausted loop caps at two iterations",
    "FLOOP.F33": "runtime guard 'if not approved:' - the running Session posture is asserted only on the exhausted one-shot arm",
    "FLOOP.F34": "runtime guard 'if not approved:' - exactly-once chat publication is asserted only on the exhausted one-shot arm",
}
NESTED_CONDITIONAL_LEAVES = {
    "NFLOOP.F12": "runtime guard 'context is not None' - loop-context path agreement only binds once loop context exists",
    "NFLOOP.F13": "first-inner-iteration guard - no previous result can exist before the first inner turn",
    "NFLOOP.F14": "later-iteration else-branch guard - the first inner iteration has no previous result to compare",
    "NFLOOP.F15": "later-iteration else-branch guard - sibling of NFLOOP.F14 on the output field",
    "NFLOOP.F23": "completed-node branch of the machine output history walk",
    "NFLOOP.F24": "completed-node branch of the machine output history walk (execution metadata agreement)",
    "NFLOOP.F25": "skipped-node else-branch of the machine output history walk - skipped nodes never publish",
}

# Symbolic identities only; no credential values are ever stored here.
NESTED_DELIVERY_TERMINAL_SYMBOL = "delivery-id:nested-terminal-callback"
NESTED_STALE_NODE_SYMBOL = "stale-node-id:first-inner-review"

# Nested parents owning zero ledger sites (definition/marker/orchestration rows).
NESTED_SITE_FREE_PARENT_COUNT = 5

# Fixed charged-leaf specs in frozen ledger assert order (FLOOP.F02..F34):
# (assert_line, field, comparator, expected, operation, short_label, extra_basis)
FIXED_LEAF_SPECS: tuple[tuple[int, str, str, Any, str, str, str], ...] = (
    (168, "validation.valid", "boolean", True, "start.definition_cli_validation",
     "frozen editorial definition validates through the public CLI", ""),
    (195, "pending", "len_equals", 1, "pending.human_queue_exactly_one",
     "pending Human queue holds exactly one node at a time", ""),
    (202, "provider_control_plane.chat_send_receipts", "len_equals", 1,
     "finish_bot.provider_delivery_exactly_once",
     "Provider chat.send delivery is captured exactly once (no concurrent or duplicate receipt)",
     " receipt capture rides the mock control plane; the local seam partitions receipts 4/4 per arm"),
    (204, "provider_chat_send_receipt.authorization", "credential_binding",
     SYMBOL_CREDENTIAL_DOWNLINK, "finish_bot.provider_static_bearer_downlink",
     "Provider downlink authorization binds the static-bearer symbolic credential identity",
     " the downlink credential is purpose-bound by the registration and cleared after the run; the symbolic identity is compared, never a value"),
    (205, "provider_chat_send_receipt.body.bcn_group_id", "equals", SYMBOL_GROUP_ECHO,
     "finish_bot.group_id_echo", "chat.send body echoes the owned state-machine Group id", ""),
    (206, "provider_chat_send_receipt.body.session_id", "equals", SYMBOL_SESSION_ECHO,
     "finish_bot.session_id_echo", "chat.send body echoes the owned Session id",
     " the exhausted-arm Session is one-shot while the approved arm shares the run session"),
    (207, "provider_chat_send_receipt.body.message", "pending_render_contains",
     ("first-human-input", "second-human-input", "second-editor-result"),
     "finish_bot.completion_prompt_continuation",
     "Provider completion prompt preserves the preceding continuation verbatim",
     " eight finish_bot deliveries (four per arm); the arm-dependent fourth member is polish-result or rewrite-result and is disclosed here, never silently merged"),
    (216, "bot_events_final_response.error_present", "boolean", False,
     "finish_bot.events_final_intake_clean",
     "final /bot/events intake answers without an error after explicit transport-conflict retry",
     " the retry predicate targets the explicit transport_conflict rejection with a bounded waiter; it never degrades to a timeout or lost ACK"),
    (222, "pending_first.loop_context.iteration", "equals", 1,
     "exercise.first_iteration_marker", "first pending Human node sits at loop iteration 1", ""),
    (223, "pending_first.loop_context.previous_result", "is_none", None,
     "exercise.first_iteration_no_previous_result", "first iteration binds no previous result", ""),
    (227, "pending_second.loop_context.iteration", "equals", 2,
     "exercise.second_iteration_marker", "second pending Human node sits at loop iteration 2", ""),
    (228, "response_refs", "distinct", (SYMBOL_REF_FIRST, SYMBOL_REF_SECOND),
     "exercise.response_ref_rotation", "the two pending nodes carry distinct response refs", ""),
    (229, "pending_second.loop_context.previous_result.output", "equals", "first-editor-result",
     "exercise.previous_output_binding", "second iteration binds the first editor result as previous output", ""),
    (230, "pending_second.loop_context.previous_result.outcome", "equals", "revise",
     "exercise.previous_outcome_binding", "second iteration binds the revise outcome of the first iteration", ""),
    (233, "pending_after_stale_respond.response_ref", "equals", SYMBOL_REF_SECOND,
     "exercise.stale_respond_queue_isolation",
     "a stale execution respond is rejected (409) and the pending queue still points at the second node", ""),
    (235, "cli_pending_render.text", "pending_render_contains",
     ("first-editor-result", SYMBOL_NODE_SECOND), "exercise.pending_render_continuity",
     "rendered pending text preserves the prior editor result and the second node identity", ""),
    (247, "run.output", "equals", "final-editorial-output", "verify.final_output",
     "completed Run output equals the frozen final editorial output", ""),
    (250, "graph_nodes.branch_completed", "branch_completed",
     (("approved", "polish", "completed"), ("exhausted", "rewrite", "completed")),
     "verify.branch_completed_routing", "completed branch routes polish on approval and rewrite on exhaustion",
     " dual-arm triple encoding: the single charged site covers both frozen arms"),
    (251, "graph_nodes.branch_skipped", "branch_skipped",
     (("approved", "rewrite", "skipped"), ("exhausted", "polish", "skipped")),
     "verify.branch_skipped_dormancy", "the non-routed branch stays skipped on both arms", ""),
    (254, "iteration_three_nodes", "unreached_iteration_skips", (2, ("skipped", "skipped")),
     "verify.unreached_iteration_skipped",
     "the unreached third-iteration nodes stay skipped (approved loop only)",
     ""),
    (257, "second_iteration_review.outcome", "second_iteration_review_outcome",
     ("approved", "revise"), "verify.second_iteration_review_outcome",
     "second-iteration review outcome is scripted approve on the approved arm and revise on the exhausted arm", ""),
    (259, "loop_route_edge.logical_outcome", "loop_route_logical_outcome",
     ("approved", "exhausted"), "verify.loop_route_logical_outcome",
     "loop-route edge records the logical outcome approved or exhausted per arm", ""),
    (263, "session_history.output_event_count", "history_output_count", (6, 6),
     "verify.history_output_events",
     "each completed execution publishes exactly one output event (six per arm across both Runs)", ""),
    (267, "run_nodes.graph_status_agreement", "equals", True,
     "verify.run_nodes_graph_status_agreement",
     "Run view node statuses agree with the graph view node statuses", ""),
    (268, "node_detail.execution_metadata_agreement", "equals", True,
     "verify.execution_metadata_agreement",
     "node detail, graph node and Run execution metadata agree exactly", ""),
    (271, "skipped_node.output_event_match_count", "equals", 0,
     "verify.skipped_node_publishes_nothing",
     "skipped nodes publish no output event (empty matching projection)",
     " the story predicate is 'not matching'; the projection freezes its cardinality 0"),
    (273, "completed_node.output_event_match_count", "equals", 1,
     "verify.completed_node_publishes_once", "each completed node matches exactly one output event", ""),
    (274, "output_event.content", "equals", SYMBOL_ARTIFACT,
     "verify.output_event_artifact_equality",
     "the matched output event content equals the node artifact text", ""),
    (275, "output_event.execution_metadata_agreement", "equals", True,
     "verify.output_event_execution_metadata",
     "the matched output event execution metadata equals the node detail execution", ""),
    (279, "cli_graph_reload.graph_equality", "equals", True,
     "verify.cli_graph_reload_equality", "CLI graph reload is byte-identical to the first graph read", ""),
    (281, "workflow_history.reload_equality", "equals", True,
     "verify.workflow_history_reload_equality",
     "Session history reload preserves this Run's records and the once-published result",
     " the reload predicate must not require unrelated Chat traffic to stop; routed Bot replies may arrive after Run completion"),
    (285, "session.status", "equals", "running", "verify.one_shot_session_running",
     "the one-shot Session stays running after Run completion (exhausted arm)",
     ""),
    (289, "chat_published_messages.count", "equals", 1,
     "verify.chat_publication_exactly_once",
     "the final editorial output reaches Chat exactly once, distinct from the six node output events",
     ""),
)

# Nested charged-leaf specs in frozen ledger assert order (NFLOOP.F02..F25).
NESTED_LEAF_SPECS: tuple[tuple[int, str, str, Any, str, str, str], ...] = (
    (172, "validation.valid", "boolean", True, "start_definition.definition_cli_validation",
     "every frozen nested definition validates through the public CLI", ""),
    (208, "provider_requests.delivery_ids", "delivery_ids_unchanged",
     {"before": [SYMBOL_DELIVERY_EARLY, NESTED_DELIVERY_TERMINAL_SYMBOL],
      "after": [SYMBOL_DELIVERY_EARLY, NESTED_DELIVERY_TERMINAL_SYMBOL]},
     "replay_terminal_callback.delivery_ids_unchanged",
     "replaying an already-complete terminal callback keeps the Provider request ledger id-identical", ""),
    (209, "events_final_response.created", "no_created_flag", {},
     "replay_terminal_callback.no_duplicate_creation",
     "the terminal callback replay answers without creating a duplicate delivery or branch", ""),
    (240, "early_break_delivery", "truthy", SYMBOL_DELIVERY_EARLY,
     "exercise_bot_story.early_inner_approval_break",
     "the early inner-approval break delivery is captured and symbolic non-empty",
     " the early break happens at inner iteration 1 approval and saves the stale-fence replay target"),
    (247, "graph.loops", "set_equals", ["outer", "outer/inner"],
     "verify_bot_story.graph_loops", "Bot graph exposes exactly the outer and outer/inner loop frames", ""),
    (248, "graph.nodes.length", "len_equals", 17, "verify_bot_story.graph_node_count",
     "nested Bot graph shape freezes 17 nodes", ""),
    (251, "graph.nodes.completed_and_skipped", "equals", [15, 2],
     "verify_bot_story.graph_completed_skipped",
     "Bot graph freezes 15 completed and 2 skipped nodes", ""),
    (254, "skipped_nodes.loop_frames", "loop_frames_equals", [["outer", 1], ["inner", 3]],
     "verify_bot_story.skipped_loop_frames",
     "the two skipped Bot nodes sit at outer iteration 1 and inner iteration 3", ""),
    (268, "pending_nodes", "len_equals", 1, "exercise_human_story.pending_exactly_one",
     "nested Human pending queue consumes exactly one node at a time", ""),
    (272, "pending.loop_path.depth", "depth_bounds", [1, 3],
     "exercise_human_story.loop_path_depth_bounds",
     "Human loop path depth stays within the frozen 1..3 bounds", ""),
    (275, "pending.loop_context.loop_path_agreement", "equals", True,
     "exercise_human_story.loop_context_path_agreement",
     "bound loop context agrees with the execution loop path",
     ""),
    (279, "first_inner.loop_context.previous_result", "is_none", None,
     "exercise_human_story.first_inner_no_previous",
     "the first inner iteration binds no previous result (parent isolation)", ""),
    (283, "inner_previous.execution_node_id", "equals", SYMBOL_EXECUTION_NODE_FIRST_INNER,
     "exercise_human_story.inner_previous_execution_node",
     "later inner iterations bind the exact first inner execution node id", ""),
    (284, "inner_previous.output", "equals", SYMBOL_HUMAN_OUTPUT_FIRST_INNER,
     "exercise_human_story.inner_previous_output",
     "later inner iterations bind the exact first inner human output", ""),
    (292, "first_inner.entries", "len_equals", 4, "exercise_human_story.first_inner_frame_count",
     "inner review publishes exactly four first-iteration frames (3 iterations x depth frames + seed)", ""),
    (293, "stale_node_id", "truthy", NESTED_STALE_NODE_SYMBOL,
     "exercise_human_story.stale_reply_rejected",
     "the stale inner node identity is captured symbolic non-empty and its re-reply is rejected with 409", ""),
    (304, "graph.loops", "set_equals", ["outer", "outer/middle", "outer/middle/inner"],
     "verify_human_story.graph_loops",
     "Human graph exposes exactly the outer, outer/middle and outer/middle/inner loop frames", ""),
    (305, "graph.nodes.length", "len_equals", 31, "verify_human_story.graph_node_count",
     "nested Human graph shape freezes 31 nodes", ""),
    (308, "graph.nodes.completed_and_skipped", "equals", [21, 10],
     "verify_human_story.graph_completed_skipped",
     "Human graph freezes 21 completed and 10 skipped nodes", ""),
    (309, "skipped_nodes.outer_third_iteration", "boolean", True,
     "verify_human_story.skipped_outer_third_iteration",
     "every skipped Human node sits at outer iteration 3 (outer subtree skip)", ""),
    (316, "history.output_event_count", "history_output_count", [15, 21],
     "verify_history.output_event_counts",
     "machine output history publishes exactly 15 Bot and 21 Human output events", ""),
    (324, "completed_node.output_event_match_count", "len_equals", 1,
     "verify_history.completed_node_publishes_once",
     "each completed node matches exactly one output event", ""),
    (325, "output_event.execution_metadata_agreement", "equals", True,
     "verify_history.output_execution_metadata",
     "the matched output event execution metadata equals the graph node execution", ""),
    (327, "skipped_node.output_event_matches", "boolean", False,
     "verify_history.skipped_node_publishes_nothing",
     "skipped nodes never publish an output event", ""),
)

# Fixed transport seam catalog: site_id -> (method, symbolic_path, path_needle,
# expected_status, accepted_statuses, note). Plane is derived from the bytes.
FIXED_SEAM_RULES: tuple[tuple[str, str, str, Any, tuple, str], ...] = (
    ("FIXED_LOOP.setup.post-136-01", "POST", "/control/reset", "/control/reset", 200, (200,),
     "story fixture reset rides the mock control plane only; the owned-local executor replaces the shared reset with a per-Run immutable Provider/Judge sandbox and verifies zero shared resets"),
    ("FIXED_LOOP.setup.post-137-01", "POST", "/me/ensure-human", "/me/ensure-human", 200, (200, 201),
     "Human fixture admission through the original business API; owned-local seam exact 200 admitting 201"),
    ("FIXED_LOOP.setup.post-138-01", "POST", "/providers", '"name": "Loop E2E provider"', 200, (200,),
     "static-bearer Provider registration with mock sink webhook URL; local seam stores purpose-bound provider-admin and downlink credential rows only"),
    ("FIXED_LOOP.setup.post-144-01", "POST", "/providers/{provider_id}/bots", "provider_bot_ref", 200, (200,),
     "Provider Bot publication carries the symbolic provider-admin downlink authorization header; the credential is purpose-bound, never stored, and cleared after the run"),
    ("FIXED_LOOP.setup.request-154-01", "PUT", "/bots/{bot_uuid}/visibility", 'method="PUT"', 200, (200,),
     "bot-self credential path: the fixed story publishes visibility under the bot runtime token header exactly to keep that path under coverage; the nested story deliberately diverges to the human authority edge"),
    ("FIXED_LOOP.setup.post-159-01", "POST", "/friends/request", "/friends/request", 200, (200, 201),
     "driver-to-worker friendship bootstrap; owned-local seam admits 201"),
    ("FIXED_LOOP.start.post-162-01", "POST", "/control/provider/clear", "/control/provider/clear", 200, (200,),
     "per-Run Provider receipt ledger clear on the mock control plane before the scripted Judge fixture"),
    ("FIXED_LOOP.start.post-163-01", "POST", "/control/judge/outcomes", "/control/judge/outcomes", 200, (200,),
     "scripted deterministic Judge admissions (revise/approved or revise/revise per arm); no live judge request is ever issued"),
    ("FIXED_LOOP.start.cli-167-01", "CLI", "bcs-cli validate", '"validate"', "cli-exit-0", ("cli-exit-0",),
     "story validates the frozen definition through the public CLI exit 0; owned-local seam also verifies POST /collaboration/definitions/validate exact 200"),
    ("FIXED_LOOP.start.post-179-01", "POST", "/groups", '"/groups", group_body', 200, (200, 201),
     "approved arm creates the state-machine Group with manual worker binding; the exhausted arm posts the plain Group; owned-local seam exact 200 admitting 201"),
    ("FIXED_LOOP.start.post-183-01", "POST", "/groups/{group_id}/state-machine-runs", "state-machine-runs", 202, (202,),
     "approved arm starts the state-machine Run with the editorial input; owned-local seam canonicalizes 202"),
    ("FIXED_LOOP.start.post-185-01", "POST", "/groups/{group_id}/sessions", '"One-shot editorial trial"', 200, (200, 201),
     "exhausted arm creates the one-shot Session titled the editorial trial; owned-local seam exact 200 admitting 201"),
    ("FIXED_LOOP.start.cli-187-01", "CLI", "bcs-cli run --session --binding worker= --input", '"run"', "cli-exit-0", ("cli-exit-0",),
     "exhausted arm admits the one-shot Run through the CLI with manual worker binding and the editorial input JSON; the owned-local identity operation consumes the purpose-bound credential in-module and never exposes it"),
    ("FIXED_LOOP.pending.request-193-01", "GET", "/state-machine-runs/{run_id}/pending-human-nodes", "pending-human-nodes", 200, (200,),
     "bounded poll for the pending Human node; story predicate is truthiness of the pending view"),
    ("FIXED_LOOP.pending.cli-194-01", "CLI", "bcs-cli query --run --pending", '"query", "--run"', "cli-exit-0", ("cli-exit-0",),
     "pending Human node read through the public CLI JSON mode"),
    ("FIXED_LOOP.finish_bot.request-199-01", "GET", "/control/provider/requests", "/control/provider/requests", 200, (200,),
     "Provider chat.send receipt capture through the mock control plane; waiter predicate targets any chat.send receipt"),
    ("FIXED_LOOP.finish_bot.post-208-01", "POST", "/control/provider/clear", "/control/provider/clear", 200, (200,),
     "inter-arm Provider receipt ledger clear on the mock control plane"),
    ("FIXED_LOOP.finish_bot.post-212-01", "POST", "/bot/events", "/bot/events", 200, (200, 409),
     "final event intake with headers X-BCN-Provider-Id and the bot runtime bearer identity; story accepts (200, 409) and the waiter retries the explicit transport_conflict rejection only, never a timeout or lost ACK"),
    ("FIXED_LOOP.exercise.cli-224-01", "CLI", "bcs-cli respond --run --node --content first-human-input", "first-human-input", "cli-exit-0", ("cli-exit-0",),
     "first Human input submission through the public CLI"),
    ("FIXED_LOOP.exercise.post-232-01", "POST", "/state-machine-runs/{run_id}/nodes/{node_id}/respond", "stale input", 409, (409,),
     "stale execution respond is rejected with 409 exactly as the story freezes it; the pending queue is never consumed by the stale reply"),
    ("FIXED_LOOP.exercise.cli-234-01", "CLI", "bcs-cli query --run --pending --text", "text=True", "cli-exit-0", ("cli-exit-0",),
     "rendered pending text mode read used by the rendered-continuation assertion"),
    ("FIXED_LOOP.exercise.cli-236-01", "CLI", "bcs-cli respond --run --node --content second-human-input", "second-human-input", "cli-exit-0", ("cli-exit-0",),
     "second Human input submission through the public CLI"),
    ("FIXED_LOOP.verify.request-245-01", "GET", "/state-machine-runs/{run_id}", "completed workflow", 200, (200,),
     "bounded completion poll; story predicate is run status completed"),
    ("FIXED_LOOP.verify.cli-246-01", "CLI", "bcs-cli query --run", '"query", "--run"', "cli-exit-0", ("cli-exit-0",),
     "Run view read through the public CLI"),
    ("FIXED_LOOP.verify.cli-248-01", "CLI", "bcs-cli query --run --graph", "--graph", "cli-exit-0", ("cli-exit-0",),
     "graph view read through the public CLI"),
    ("FIXED_LOOP.verify.request-261-01", "GET", "/sessions/{session_id}/messages", "history_path", 200, (200,),
     "Session message history read; the workflow_history projection selects this Run's records plus its once-published result"),
    ("FIXED_LOOP.verify.cli-266-01", "CLI", "bcs-cli query --run --node", "--node", "cli-exit-0", ("cli-exit-0",),
     "per-node detail read through the public CLI for the execution-metadata agreement walk"),
    ("FIXED_LOOP.verify.cli-279-01", "CLI", "bcs-cli query --run --graph (reload)", "--graph", "cli-exit-0", ("cli-exit-0",),
     "reload equality site: the CLI graph re-read must equal the first graph read byte-for-byte"),
    ("FIXED_LOOP.verify.request-280-01", "GET", "/sessions/{session_id}/messages (reload)", "reloaded_history", 200, (200,),
     "history reload immutability site; unrelated Chat traffic need not stop"),
    ("FIXED_LOOP.verify.request-284-01", "GET", "/sessions/{session_id}", '"/sessions/{segment(self.session_id)}"', 200, (200,),
     "one-shot Session view read on the exhausted leg; the Session must remain running after Run completion"),
    ("FIXED_LOOP.cleanup.request-293-01", "DELETE", "/groups/{group_id}?bot_id={driver_bot_id}", 'method="DELETE"', 200, (200,),
     "exact cleanup deletes the two disposable Groups through the original business API with the driver bot id qualifier; owned-local seam confirms the deleted flag and id"),
    ("FIXED_LOOP.cleanup.request-295-01", "DELETE", "/providers/{provider_id}/bots/{bot_uuid}", 'method="DELETE"', 200, (200,),
     "Provider Bot retirement through the original business API under the symbolic provider-admin authorization header; non-target state stays untouched (0 deletions)"),
    ("FIXED_LOOP.cleanup.post-297-01", "POST", "/control/reset", "/control/reset", 200, (200,),
     "per-Run fixture restore on the mock control plane inside the owned sandbox; the shared broad reset stays replaced"),
)

# Nested transport seam catalog: site_id -> (method, symbolic_path, path_needle,
# expected_status, accepted_statuses, note).
NESTED_SEAM_RULES: tuple[tuple[str, str, str, Any, tuple, str], ...] = (
    ("NESTED-LOOP.setup.post-146-01", "POST", "/control/reset", "/control/reset", 200, (200,),
     "story fixture reset rides the mock control plane only; identical fixture posture to the fixed family inside the child process"),
    ("NESTED-LOOP.setup.post-147-01", "POST", "/me/ensure-human", "/me/ensure-human", 200, (200, 201),
     "Human fixture admission through the original business API"),
    ("NESTED-LOOP.setup.post-148-01", "POST", "/providers", '"name": "Nested Loop E2E provider"', 200, (200,),
     "static-bearer Provider registration with mock sink webhook URL and native-tool coordination mode"),
    ("NESTED-LOOP.setup.post-154-01", "POST", "/providers/{provider_id}/bots", "provider_bot_ref", 200, (200,),
     "disposable release-domain worker Bot publication under the symbolic provider-admin downlink header; the admin-minted Human authority edge is the intended coverage target"),
    ("NESTED-LOOP.setup.request-163-01", "PUT", "/bots/{bot_uuid}/visibility", 'method="PUT"', 200, (200,),
     "human authority edge divergence: the nested story publishes visibility through the regular human identity headers with no bot-self credential header (fixed story uses the bot-self credential path); provider-admin publication mints the owner Human authority edge first"),
    ("NESTED-LOOP.start_definition.post-168-01", "POST", "/control/provider/clear", "/control/provider/clear", 200, (200,),
     "per-definition Provider receipt ledger clear on the mock control plane before the frozen Bot Judge sequence"),
    ("NESTED-LOOP.start_definition.cli-171-01", "CLI", "bcs-cli validate", '"validate"', "cli-exit-0", ("cli-exit-0",),
     "every nested definition (Bot and Human) validates through the public CLI exit 0 before group creation"),
    ("NESTED-LOOP.start_definition.post-173-01", "POST", "/groups", '"participant_bindings"', 200, (200, 201),
     "state-machine Group creation with deduplicated participants (driver bot, worker bot, human_) and manual worker binding; owned-local analog adopts exact 200 admitting 201"),
    ("NESTED-LOOP.start_definition.post-187-01", "POST", "/groups/{group_id}/state-machine-runs", "state-machine-runs", 202, (202,),
     "state-machine Run start with the story-name input; canonical 202 posture identical to the fixed family seam"),
    ("NESTED-LOOP.replay_terminal_callback.request-197-01", "GET", "/control/provider/requests", "/control/provider/requests", 200, (200,),
     "bounded capture of the next Provider request before the terminal callback replay"),
    ("NESTED-LOOP.replay_terminal_callback.post-199-01", "POST", "/bot/events", "/bot/events", 200, (200, 409, 410),
     "terminal callback replay fence: the story accepts (200, 409, 410) for an already-complete inner delivery; headers carry X-BCN-Provider-Id and the bot runtime bearer identity"),
    ("NESTED-LOOP.replay_terminal_callback.request-207-01", "GET", "/control/provider/requests", "/control/provider/requests", 200, (200,),
     "post-replay Provider ledger read; the id list must equal the pre-replay ledger exactly"),
    ("NESTED-LOOP.exercise_bot_story.post-212-01", "POST", "/control/judge/outcomes", '"revise", "approved", "repeat"', 200, (200,),
     "scripted seven-outcome Bot Judge fixture (revise, approved, repeat, revise, revise, revise, done) on the mock control plane"),
    ("NESTED-LOOP.verify_bot_story.request-244-01", "GET", "/state-machine-runs/{run_id}", "nested Bot completion", 200, (200,),
     "bounded completion poll for the nested Bot story Run"),
    ("NESTED-LOOP.verify_bot_story.cli-246-01", "CLI", "bcs-cli query --run --graph", "--graph", "cli-exit-0", ("cli-exit-0",),
     "Bot graph read through the public CLI; basis of the 17/15/2 shape freezes"),
    ("NESTED-LOOP.exercise_human_story.post-260-01", "POST", "/control/judge/outcomes", '"repeat", "done"', 200, (200,),
     "scripted two-outcome Human Judge fixture (repeat, done) on the mock control plane before the three-level Human story"),
    ("NESTED-LOOP.exercise_human_story.request-266-01", "GET", "/state-machine-runs/{run_id}/pending-human-nodes", "nested Human input", 200, (200,),
     "bounded poll consuming the three-level Human pending queue exactly one node at a time"),
    ("NESTED-LOOP.exercise_human_story.post-286-01", "POST", "/state-machine-runs/{run_id}/nodes/{node_id}/respond", "/respond", 200, (200,),
     "Human content respond on the current pending node with the scripted human-result-{index} payload"),
    ("NESTED-LOOP.exercise_human_story.post-290-01", "POST", "/state-machine-runs/{run_id}/nodes/{node_id}/respond (stale fence)", "stale-inner-response", 409, (409,),
     "stale inner node re-reply is rejected with 409 exactly as the story freezes it; the first-inner node id is the replay fence target"),
    ("NESTED-LOOP.verify_human_story.request-299-01", "GET", "/state-machine-runs/{run_id}", "nested Human completion", 200, (200,),
     "bounded completion poll for the three-level Human story Run"),
    ("NESTED-LOOP.verify_human_story.request-303-01", "GET", "/state-machine-runs/{run_id}/graph", '"/graph"', 200, (200,),
     "Human authentication boundary: runs containing HumanInput are deliberately Human-authenticated and the bot runtime token used by bcs-cli must not bypass that boundary; the story reads the graph directly through the human identity"),
    ("NESTED-LOOP.verify_history.request-313-01", "GET", "/sessions/{session_id}/messages", "/sessions/", 200, (200,),
     "machine output history read for the dual-story output equality walk (15 Bot / 21 Human events)"),
)

# Cross-family, NON-additive inheritance annex (nested child process inherits
# the base fixed_loop_story.Story.finish_bot assert family and the cleanup
# traffic stations; the nested ledger stays 46 and never absorbs them).
INHERITED_BASE_ANCHORS = {
    "annex": "non_additive_cross_family_base_inheritance",
    "base_story_module": "fixed_loop_story.Story",
    "inherited_assert_family": (
        "FIXED_LOOP.finish_bot.assert-202-01", "FIXED_LOOP.finish_bot.assert-204-01",
        "FIXED_LOOP.finish_bot.assert-205-01", "FIXED_LOOP.finish_bot.assert-206-01",
        "FIXED_LOOP.finish_bot.assert-207-01", "FIXED_LOOP.finish_bot.assert-216-01",
    ),
    "inherited_transport_stations": (
        "FIXED_LOOP.cleanup.request-293-01", "FIXED_LOOP.cleanup.request-295-01",
        "FIXED_LOOP.cleanup.post-297-01",
    ),
    "additive": False,
    "basis": (
        "the nested child process subclasses the fixed Story and reuses finish_bot plus the "
        "teardown without re-declaring them; the annex discloses the six inherited finish_bot "
        "assert atoms (202, 204, 205, 206, 207, 216) and the three inherited cleanup transport "
        "stations (293, 295, 297) with their FIXED_LOOP.* ledger ids, keeps the nested ledger "
        "at 46 sites (never inflated), and never double-charges the inherited stations as nested "
        "density rows; their primary charge stays on the fixed family roster"),
}

FIXED_CHARGED_REGISTRATIONS = (FACE_UNDERCOUNT_ID, DUAL_FACE_DIVERGENCE_ID, FIXED_PREPROD_GAP_ID)
NESTED_CHARGED_REGISTRATIONS = (FACE_UNDERCOUNT_ID, DUAL_FACE_DIVERGENCE_ID, NESTED_PREPROD_GAP_ID)


def _leaf_id(prefix: str, index: int) -> str:
    return f"{prefix}{index:02d}"


def _legs_for_fixed_line(line: int) -> tuple[str, ...]:
    return FIXED_ARM_LEG_RULES.get(line, FIXED_DEFAULT_LEGS)


def _arm_leg_text(legs: tuple[str, ...]) -> str:
    if legs == FIXED_DEFAULT_LEGS:
        return "dual approved/exhausted arms"
    if legs == ("approved",):
        return "dormant unless the approved arm executes (arm rule frozen from the story guard)"
    return "dormant unless the exhausted arm executes (arm rule frozen from the story guard)"


def _leaf_basis(*, site_id: str, source_file: str, line_start: int, line_end: int,
                function: str, comparator: str, field: str, primary: str, co_parents: tuple,
                legs_text: str, conditional: str, extra: str) -> str:
    co = ", ".join(co_parents) if co_parents else "none"
    span = f"lines {line_start}-{line_end}" if line_end != line_start else f"line {line_start}"
    return (
        f"python assert atom {site_id} (function {function}, {source_file} {span}); comparator "
        f"{comparator} on field {field} against the frozen expected value; primary parent "
        f"{primary}; co-owning parents: {co}; legs: {legs_text}; charged exactly once as a "
        f"source-anchored density leaf{conditional};{extra} upstream poolab color "
        f"{UPSTREAM_NOT_ROSTERED} (no per-check receipt retained for the dynamic owned-local families)")


def _parent_basis_fixed(envelope: dict[str, Any], transport: tuple[str, ...],
                        assert_sites: tuple[str, ...], legs: tuple[str, ...]) -> str:
    checkpoints = len(transport)
    return (
        f"migration-contract envelope reused verbatim from {FIXED_CONTRACT_FILE} (kind "
        f"{envelope['kind']}, method {envelope['method']}, path {envelope['path']}, actor "
        f"{envelope['actor']}); owns {checkpoints} parent-internal transport checkpoint(s) and "
        f"{len(assert_sites)} assert-site lineage disclosures (blood relation only, never charged "
        f"as rows); legs: {_arm_leg_text(legs)}; the union of all parent-internal checkpoints "
        f"equals the 33-site transport ledger exactly once each; transport stations ride this "
        f"envelope as internal checkpoints and are never double-charged; upstream poolab color "
        f"{UPSTREAM_NOT_ROSTERED}")


def _parent_basis_nested(envelope: dict[str, Any], transport: tuple[str, ...],
                         assert_sites: tuple[str, ...], legs: tuple[str, ...]) -> str:
    site_free = not transport and not assert_sites
    annex = ""
    if site_free:
        annex = (" site-free definition/marker/orchestration parent: zero ledger sites anchor "
                 "here by design (the story structure and the completion markers are frozen by "
                 "the contract row itself, not by executor ledger traffic)")
    markers_text = "/".join(legs)
    return (
        f"migration-contract envelope reused verbatim from {NESTED_CONTRACT_FILE} at frozen "
        f"story span {envelope['story_lines'][0]}-{envelope['story_lines'][1]} (legs equal the "
        f"frozen story markers {markers_text}); owns {len(transport)} parent-internal transport "
        f"checkpoint(s) and {len(assert_sites)} assert-site lineage disclosures (blood relation "
        f"only, never charged as rows);{annex} the union of all parent-internal checkpoints "
        f"equals the 22-site transport ledger exactly once each; transport stations ride this "
        f"envelope as internal checkpoints and are never double-charged; upstream poolab color "
        f"{UPSTREAM_NOT_ROSTERED}")


def _sh_assert_line_bytes(line_no: int) -> str:
    return wrapper_sh_story_lines()[line_no - 1]


def _validate_leaf_specs(family: str) -> None:
    ledger = site_span_index(family)
    prefix = FIXED_PREFIX if family == FAMILY_FIXED else NESTED_PREFIX
    site_prefix = "FIXED_LOOP." if family == FAMILY_FIXED else "NESTED-LOOP."
    specs = FIXED_LEAF_SPECS if family == FAMILY_FIXED else NESTED_LEAF_SPECS
    assert_sites = [record for record in site_records(family) if record.kind == KIND_ASSERT]
    if len(specs) != len(assert_sites):
        raise RunStateError(f"{family} leaf spec count {len(specs)} != assert ledger {len(assert_sites)}")
    for spec, record in zip(specs, assert_sites):
        if record.start != spec[0]:
            raise RunStateError(
                f"{family} leaf spec line {spec[0]} drifted from ledger site {record.site_id} at {record.start}")
        expected_id = f"{site_prefix}{record.function}.assert-{record.start:03d}-01"
        if record.site_id != expected_id:
            raise RunStateError(f"{family} ledger site grammar drifted: {record.site_id}")
        if record.kind != KIND_ASSERT:
            raise RunStateError(f"{family} leaf spec anchored a non-assert site: {record.site_id}")
        if not record.site_id.startswith(site_prefix):
            raise RunStateError(f"{family} ledger site family prefix drifted: {record.site_id}")
        leaf_id = _leaf_id(prefix, len([s for s in specs if s[0] < spec[0]]) + 2)
        if not leaf_id.startswith(prefix):
            raise RunStateError(f"{family} leaf id grammar drifted: {leaf_id}")


def _derived_plane(quote: str, method: str, symbolic_path: str) -> str:
    if method == "CLI" or "self.cli(" in quote:
        return "cli"
    if "mock=True" in quote or symbolic_path.startswith("/control/"):
        return "mock-control-plane"
    return "carrier"


def _seam_validation(family: str, rules: tuple) -> None:
    ledger = site_span_index(family)
    lines = story_lines(family)
    transport_total = (FIXED_TRANSPORT_SITE_COUNT if family == FAMILY_FIXED
                       else NESTED_TRANSPORT_SITE_COUNT)
    seen: set[str] = set()
    for site_id, method, path, needle, expected, accepted, note in rules:
        if site_id not in ledger:
            raise RunStateError(f"{family} seam rule anchors an unknown site: {site_id}")
        record = ledger[site_id]
        if record.kind != KIND_TRANSPORT:
            raise RunStateError(f"{family} seam rule anchors a non-transport site: {site_id}")
        if site_id in seen:
            raise RunStateError(f"{family} seam rule duplicates site: {site_id}")
        seen.add(site_id)
        quote = "\n".join(lines[record.start - 1:record.end])
        if needle not in quote:
            raise RunStateError(f"{family} seam path needle drifted for {site_id}: {needle!r}")
        if not isinstance(accepted, tuple) or not accepted or expected not in accepted:
            raise RunStateError(f"{family} seam accepted-set posture drifted for {site_id}")
        derived_method = "CLI" if "self.cli(" in quote else (
            "PUT" if 'method="PUT"' in quote else (
                "DELETE" if 'method="DELETE"' in quote else (
                    "POST" if "self.post(" in quote else "GET")))
        if derived_method != method:
            raise RunStateError(f"{family} seam method drifted for {site_id}: derived {derived_method}")
        plane = _derived_plane(quote, method, path)
        if plane == "mock-control-plane" and "mock=True" not in quote:
            raise RunStateError(f"{family} seam plane drifted for {site_id}: control plane without mock=True")
        if plane == "carrier" and ("mock=True" in quote or "self.cli(" in quote):
            raise RunStateError(f"{family} seam plane drifted for {site_id}: carrier site on mock/cli bytes")
        if "status=(200, 409, 410)" in quote:
            if set(accepted) != {200, 409, 410} or expected != 200:
                raise RunStateError(f"{family} seam fence statuses drifted for {site_id}")
        elif "status=(200, 409)" in quote:
            if set(accepted) != {200, 409} or expected != 200:
                raise RunStateError(f"{family} seam fence statuses drifted for {site_id}")
        elif "status=409" in quote:
            if accepted != (409,) or expected != 409:
                raise RunStateError(f"{family} seam stale-respond posture drifted for {site_id}")
    if len(seen) != transport_total:
        raise RunStateError(f"{family} seam rule count {len(seen)} != transport ledger {transport_total}")
    transport = {record.site_id for record in site_records(family)
                 if record.kind == KIND_TRANSPORT}
    if seen != transport:
        missing = sorted(transport - seen)
        extra = sorted(seen - transport)
        raise RunStateError(f"{family} transport seam coverage drifted; missing={missing} extra={extra}")


def _wrapper_rows(family: str) -> tuple[Obligation, Obligation]:
    """Return the gate envelope row and the charged facade leaf row."""
    fixed = family == FAMILY_FIXED
    envelope = wrapper_gate_envelope(family)
    gate_id = GATE_ID_FIXED if fixed else GATE_ID_NESTED
    facade_id = FIXED_WRAPPER_LEAF if fixed else NESTED_WRAPPER_LEAF
    prefix = FIXED_PREFIX if fixed else NESTED_PREFIX
    parent_count = FIXED_PARENT_COUNT if fixed else NESTED_PARENT_COUNT
    legs = FIXED_DEFAULT_LEGS if fixed else NESTED_MARKER_LEGS
    span = FIXED_SH_STORY_SPAN if fixed else NESTED_SH_STORY_SPAN
    assert_line = FIXED_SH_ASSERT_LINE if fixed else NESTED_SH_ASSERT_LINE
    gate_basis = (
        f"face-derived auxiliary pseudo-envelope from {FIXED_SH_FILE} story function span "
        f"{span[0]}-{span[1]}; owns the facade leaf exit-0 assertion; disclosed as derived from "
        f"the executor-migration face wrapper, never as a migration-contract obligation; "
        f"excluded from the charged parent caliber ({parent_count} contract parents stay "
        f"charged for this family)")
    gate = Obligation(
        obligation_id=gate_id, story=(STORY_FIXED if fixed else STORY_NESTED),
        level="PARENT", charged=False, envelope_source=FACE_WRAPPER_GATE,
        parent_obligation="", co_parents=(), legs=legs, ledger_site_id=None,
        method=envelope["method"], operation="face_wrapper_gate_envelope",
        path=envelope["path"], actor=envelope["actor"], kind=envelope["kind"],
        field=None, comparator="envelope", expected=STATUS_PASS,
        line_start=envelope["line_start"], line_end=envelope["line_end"],
        quote=envelope["quote"], source_file=FIXED_SH_FILE,
        expectation=envelope["expectation"],
        upstream_color=UPSTREAM_NOT_ROSTERED,
        upstream_color_basis=B_UPSTREAM_NOT_ROSTERED,
        named_registrations=(), leaf_classification=GATE_CLASSIFICATION,
        basis=gate_basis, internal_checkpoints=(), internal_assert_sites=(),
        story_lines=(), markers=(), located=True)
    facade_basis = (
        f"wrapper shell facade exit-0 assertion at {FIXED_SH_FILE}:{assert_line}; charged once "
        f"as the facade leaf under the disclosed uncharged auxiliary gate {gate_id}; the "
        f"pseudo-envelope lineage (face wrapper mode DUAL bookkeeping) is disclosed on the plan "
        f"wrapper_facade block; the face parsed snapshot counts this facade alone, which is the "
        f"registered undercount gap")
    facade = Obligation(
        obligation_id=facade_id, story=gate.story, level="LEAF", charged=True,
        envelope_source=FACE_WRAPPER_GATE, parent_obligation=gate_id, co_parents=(), legs=legs,
        ledger_site_id=None, method="assert_eq",
        operation=("fixed_loop_story_shell_facade" if fixed
                   else "nested_fixed_loop_story_shell_facade"),
        path=FIXED_SH_FILE, actor="LOCAL_CONTROL", kind="shell_facade_assert",
        field="exit_code", comparator="exit_code_equals", expected=0,
        line_start=assert_line, line_end=assert_line,
        quote=_sh_assert_line_bytes(assert_line), source_file=FIXED_SH_FILE,
        expectation=(f"wrapper shell facade exits 0 after its dual-arm story run and cleanup "
                     f"({envelope['facade_assert_message']})"),
        upstream_color=UPSTREAM_NOT_ROSTERED, upstream_color_basis=B_UPSTREAM_NOT_ROSTERED,
        named_registrations=(FIXED_CHARGED_REGISTRATIONS if fixed else NESTED_CHARGED_REGISTRATIONS),
        leaf_classification=FACADE_LEAF_CLASSIFICATION, basis=facade_basis,
        internal_checkpoints=(), internal_assert_sites=(), story_lines=(), markers=(),
        located=True)
    return gate, facade


def _fixed_parent_legs(site_ids: tuple[str, ...], ledger: dict) -> tuple[str, ...]:
    legs: tuple[str, ...] = ()
    for site_id in sorted(site_ids, key=lambda item: (ledger[item].start, item)):
        for leg in _legs_for_fixed_line(ledger[site_id].start):
            if leg not in legs:
                legs = legs + (leg,)
    return legs or FIXED_DEFAULT_LEGS


def _roster(family: str) -> tuple[Obligation, ...]:
    """Assemble one family roster with every global invariant enforced."""
    fixed = family == FAMILY_FIXED
    prefix = FIXED_PREFIX if fixed else NESTED_PREFIX
    leaf_count = FIXED_LEAF_COUNT if fixed else NESTED_LEAF_COUNT
    parent_count = FIXED_PARENT_COUNT if fixed else NESTED_PARENT_COUNT
    charged_registrations = (FIXED_CHARGED_REGISTRATIONS if fixed else NESTED_CHARGED_REGISTRATIONS)
    _validate_leaf_specs(family)
    _seam_validation(family, FIXED_SEAM_RULES if fixed else NESTED_SEAM_RULES)
    envelopes = build_fixed_parent_envelopes() if fixed else build_nested_parent_envelopes()
    if len(envelopes) != parent_count:
        raise RunStateError(f"{family} parent envelope caliber changed")
    ledger = site_span_index(family)
    ownership = dynamic_family_contract(family).assertion_requirement_ids
    rows: list[Obligation] = []
    markers_by_parent: dict[str, tuple[str, ...]] = {}
    conditional_ids = set(FIXED_CONDITIONAL_LEAVES if fixed else NESTED_CONDITIONAL_LEAVES)
    for envelope in envelopes:
        requirement_id = envelope["obligation_id"]
        owners_of = [site_id for site_id, owners in ownership.items() if requirement_id in owners]
        transport = tuple(site_id for site_id in owners_of if ledger[site_id].kind == KIND_TRANSPORT)
        assert_sites = tuple(site_id for site_id in owners_of if ledger[site_id].kind == KIND_ASSERT)
        if tuple(envelope["internal_checkpoints"]) != transport:
            raise RunStateError(f"{family} envelope checkpoint lineage drifted: {requirement_id}")
        if tuple(envelope["internal_assert_sites"]) != assert_sites:
            raise RunStateError(f"{family} envelope assert lineage drifted: {requirement_id}")
        if fixed:
            legs = _fixed_parent_legs(tuple(owners_of), ledger)
            markers: tuple[str, ...] = ()
            story_span: tuple[int, ...] = ()
            basis = _parent_basis_fixed(envelope, transport, assert_sites, legs)
        else:
            legs = tuple(envelope["markers"])
            markers = tuple(envelope["markers"])
            story_span = (envelope["line_start"], envelope["line_end"])
            markers_by_parent[requirement_id] = markers
            basis = _parent_basis_nested(envelope, transport, assert_sites, legs)
        rows.append(Obligation(
            obligation_id=requirement_id, story=(STORY_FIXED if fixed else STORY_NESTED),
            level="PARENT", charged=True,
            envelope_source=(FIXED_CONTRACT_ENVELOPE if fixed else NESTED_CONTRACT_ENVELOPE),
            parent_obligation="", co_parents=(), legs=legs, ledger_site_id=None,
            method=envelope["method"], operation=(
                "migration_contract_envelope_reuse" if fixed
                else "story_span_requirement_envelope_reuse"),
            path=envelope["path"], actor=envelope["actor"], kind=envelope["kind"],
            field=None, comparator="envelope", expected=STATUS_PASS,
            line_start=envelope["line_start"], line_end=envelope["line_end"],
            quote=envelope["quote"], source_file=envelope["source_file"],
            expectation=envelope["expectation"],
            upstream_color=UPSTREAM_NOT_ROSTERED, upstream_color_basis=B_UPSTREAM_NOT_ROSTERED,
            named_registrations=charged_registrations,
            leaf_classification=PARENT_CLASSIFICATION, basis=basis,
            internal_checkpoints=transport, internal_assert_sites=assert_sites,
            story_lines=story_span, markers=markers, located=True))
    gate, facade = _wrapper_rows(family)
    rows.append(gate)
    rows.append(facade)
    leaf_ids = [facade.obligation_id]
    specs = FIXED_LEAF_SPECS if fixed else NESTED_LEAF_SPECS
    assert_records = [record for record in site_records(family) if record.kind == KIND_ASSERT]
    index = 2
    for spec, record in zip(specs, assert_records):
        (line, field, comparator, expected, operation, label, extra) = spec
        site_id = record.site_id
        owners = ownership[site_id]
        primary = owners[0]
        co_parents = tuple(owner for owner in owners[1:])
        if fixed:
            legs = _legs_for_fixed_line(record.start)
            legs_text = _arm_leg_text(legs)
            leaf_path = f"{FIXED_PY_FILE}:{record.start}-{record.end}"
        else:
            legs = markers_by_parent[primary]
            legs_text = f"parent story markers {'/'.join(legs)}"
            leaf_path = f"{NESTED_PY_FILE}:{record.start}-{record.end}"
        leaf_id = _leaf_id(prefix, index)
        conditional = (FIXED_CONDITIONAL_LEAVES if fixed else NESTED_CONDITIONAL_LEAVES).get(leaf_id, "")
        conditional_text = f" (runtime-conditional branch: {conditional})" if conditional else ""
        span = (f"lines {record.start}-{record.end}" if record.end != record.start
                else f"line {record.start}")
        basis = (
            f"python assert atom {site_id} (function {record.function}, "
            f"{FIXED_PY_FILE if fixed else NESTED_PY_FILE} {span}); comparator {comparator} on "
            f"field {field} against the frozen expected value; primary parent {primary}; "
            f"co-owning parents: {', '.join(co_parents) if co_parents else 'none'}; legs: "
            f"{legs_text}; charged exactly once as a source-anchored density leaf"
            f"{conditional_text};{extra} upstream poolab color {UPSTREAM_NOT_ROSTERED} "
            f"(no per-check receipt retained for the dynamic owned-local families)")
        rows.append(Obligation(
            obligation_id=leaf_id, story=(STORY_FIXED if fixed else STORY_NESTED),
            level="LEAF", charged=True, envelope_source=LEDGER_LEAF_ENVELOPE_SOURCE,
            parent_obligation=primary, co_parents=co_parents, legs=legs,
            ledger_site_id=site_id, method="assert_eq", operation=operation,
            path=leaf_path, actor="STORY_PROGRAM", kind="python_assert_atom", field=field,
            comparator=comparator, expected=expected, line_start=record.start,
            line_end=record.end,
            quote="\n".join(story_lines(family)[record.start - 1:record.end]),
            source_file=(FIXED_PY_FILE if fixed else NESTED_PY_FILE),
            expectation=f"{label}; frozen comparator {comparator} projection of the story predicate",
            upstream_color=UPSTREAM_NOT_ROSTERED, upstream_color_basis=B_UPSTREAM_NOT_ROSTERED,
            named_registrations=charged_registrations,
            leaf_classification=LEAF_CLASSIFICATION, basis=basis,
            internal_checkpoints=(), internal_assert_sites=(), story_lines=(), markers=(),
            located=True))
        leaf_ids.append(leaf_id)
        index += 1
    if index - 1 != leaf_count or len(leaf_ids) != leaf_count:
        raise RunStateError(f"{family} leaf caliber changed during roster assembly")
    if conditional_ids - set(leaf_ids):
        raise RunStateError(f"{family} conditional leaf ids left the roster: {sorted(conditional_ids - set(leaf_ids))}")
    if len(rows) != parent_count + 1 + leaf_count:
        raise RunStateError(f"{family} roster row caliber changed")
    if len({row.obligation_id for row in rows}) != len(rows):
        raise RunStateError(f"{family} roster ids are not unique")
    charged = [row for row in rows if row.charged]
    if len(charged) != (FIXED_CHARGED_ROW_COUNT if fixed else NESTED_CHARGED_ROW_COUNT):
        raise RunStateError(f"{family} charged row caliber changed")
    return tuple(rows)


def build_fixed_loop_roster() -> tuple[Obligation, ...]:
    """Return the 43-row fixed-family roster (8 parents, gate, 34 leaves)."""
    return _roster(FAMILY_FIXED)


def build_nested_loop_roster() -> tuple[Obligation, ...]:
    """Return the 42-row nested-family roster (16 parents, gate, 25 leaves)."""
    return _roster(FAMILY_NESTED)


def family_checkpoint_catalog(family: str) -> dict[str, tuple[str, ...]]:
    """Map every parent to its ordered owned transport checkpoint ids."""
    ledger = site_span_index(family)
    ownership = dynamic_family_contract(family).assertion_requirement_ids
    catalog: dict[str, list[str]] = {}
    for site_id, owners in ownership.items():
        if ledger[site_id].kind != KIND_TRANSPORT:
            continue
        for requirement_id in owners:
            catalog.setdefault(requirement_id, []).append(site_id)
    return {requirement_id: tuple(site_ids) for requirement_id, site_ids in catalog.items()}


def transport_checkpoint_union(family: str) -> tuple[str, ...]:
    """Return the ordered unique transport checkpoint union (33 fixed / 22 nested).

    The union order is the frozen ledger source order, never the parent
    envelope iteration order; every transport site must be owned by at least
    one parent envelope (no un-routed station) and every ownership reference
    must resolve inside the frozen ledger (no phantom site), both fail-closed.
    """
    ledger = site_span_index(family)
    ownership = dynamic_family_contract(family).assertion_requirement_ids
    unknown = sorted(set(ownership) - set(ledger))
    if unknown:
        raise RunStateError(f"{family} ownership references unknown ledger sites: {unknown}")
    transport_order = [record.site_id for record in site_records(family)
                       if record.kind == KIND_TRANSPORT]
    transport_set = set(transport_order)
    union = [site_id for site_id in transport_order if site_id in ownership]
    if len(union) != len(transport_order) or set(union) != transport_set:
        missing = sorted(transport_set - set(union))
        raise RunStateError(
            f"{family} transport checkpoint union lost stations: missing={missing}")
    if len(set(union)) != len(union):
        raise RunStateError(f"{family} transport checkpoint union duplicated a station")
    return tuple(union)


def site_free_parents(family: str) -> tuple[str, ...]:
    """Return parent ids owning zero ledger sites (nested definition/marker/orchestration rows).

    The fixed family always anchors every envelope to at least one ledger
    site, so its site-free caliber is zero by construction; the nested family
    freezes five structure-only parents (two definition rows, two completion
    marker rows and the main orchestration row) whose story span and markers
    are carried by the contract row itself.  Fail-closed on caliber drift.
    """
    if family != FAMILY_NESTED:
        return ()
    ledger = site_span_index(family)
    ownership = dynamic_family_contract(family).assertion_requirement_ids
    transport_owners: set[str] = set()
    assert_owners: set[str] = set()
    for site_id, owners in ownership.items():
        bucket = transport_owners if ledger[site_id].kind == KIND_TRANSPORT else assert_owners
        bucket.update(owners)
    known = {envelope["obligation_id"] for envelope in build_nested_parent_envelopes()}
    free = tuple(
        requirement_id for requirement_id in known
        if requirement_id not in transport_owners and requirement_id not in assert_owners)
    if len(free) != NESTED_SITE_FREE_PARENT_COUNT:
        raise RunStateError(
            f"{family} site-free parent caliber changed: {len(free)} != "
            f"{NESTED_SITE_FREE_PARENT_COUNT}")
    return free


def inherited_base_anchor_annex(family: str) -> dict[str, Any]:
    """Return the NON-additive cross-family inheritance annex disclosure."""
    if family == FAMILY_NESTED:
        annex = dict(INHERITED_BASE_ANCHORS)
        annex["applies_to_family"] = FAMILY_NESTED
        annex["nested_ledger_site_count"] = NESTED_LEDGER_SITE_COUNT
        annex["nested_leaf_count"] = NESTED_LEAF_COUNT
        annex["nested_charged_row_count"] = NESTED_CHARGED_ROW_COUNT
        return annex
    return {
        "annex": INHERITED_BASE_ANCHORS["annex"],
        "applies_to_family": FAMILY_NESTED,
        "applies_to_fixed_family": False,
        "basis": (
            "the fixed family is the base story module (fixed_loop_story.Story); nothing is "
            "inherited into it, the annex is disclosed on the nested plan only, and the fixed "
            "calibers stay 66 ledger sites / 34 leaves / 42 charged rows regardless"),
    }


def transport_seam_rules(family: str) -> tuple:
    """Return the frozen owned-local seam rule table bytes."""
    return FIXED_SEAM_RULES if family == FAMILY_FIXED else NESTED_SEAM_RULES


def transport_seam_catalog(family: str) -> tuple[dict[str, Any], ...]:
    """Return the derived seam catalog with byte-derived plane and spans."""
    fixed = family == FAMILY_FIXED
    rules = FIXED_SEAM_RULES if fixed else NESTED_SEAM_RULES
    _seam_validation(family, rules)
    ledger = site_span_index(family)
    lines = story_lines(family)
    catalog: list[dict[str, Any]] = []
    for site_id, method, path, needle, expected, accepted, note in rules:
        record = ledger[site_id]
        quote = "\n".join(lines[record.start - 1:record.end])
        catalog.append({
            "site_id": site_id, "function": record.function, "label": record.label,
            "kind": record.kind, "line_start": record.start, "line_end": record.end,
            "method": method, "symbolic_path": path, "expected_status": expected,
            "accepted_statuses": list(accepted), "plane": _derived_plane(quote, method, path),
            "note": note})
    return tuple(catalog)


__all__ = [
    "FIXED_ARM_LEG_RULES", "FIXED_CHARGED_REGISTRATIONS", "FIXED_CONDITIONAL_LEAVES",
    "FIXED_DEFAULT_LEGS", "FIXED_LEAF_SPECS", "FIXED_SEAM_RULES",
    "FACADE_LEAF_CLASSIFICATION", "GATE_CLASSIFICATION", "INHERITED_BASE_ANCHORS",
    "KIND_ASSERT", "KIND_TRANSPORT", "LEAF_CLASSIFICATION", "LEDGER_LEAF_ENVELOPE_SOURCE",
    "NESTED_CHARGED_REGISTRATIONS", "NESTED_CONDITIONAL_LEAVES", "NESTED_LEAF_SPECS",
    "NESTED_MARKER_LEGS", "NESTED_SEAM_RULES", "NESTED_DELIVERY_TERMINAL_SYMBOL",
    "NESTED_STALE_NODE_SYMBOL", "NESTED_SITE_FREE_PARENT_COUNT", "PARENT_CLASSIFICATION",
    "build_fixed_loop_roster", "build_nested_loop_roster", "family_checkpoint_catalog",
    "inherited_base_anchor_annex", "site_free_parents", "transport_checkpoint_union",
    "transport_seam_catalog", "transport_seam_rules",
]
