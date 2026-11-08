# Workflow graph fixtures for #2608

These fixtures feed the undercover side-panel workflow board. They are derived
from the **real** referee script rather than hand-drawn, so the board is tested
against the topology the referee actually submits for a game round.

## Files

| File | Role |
| --- | --- |
| `generate_fixtures.py` | Provenance/regeneration script. Imports the referee script, renders the YAML below, strips private content, and emits `../workflow-graphs.mjs`. |
| `speak-regular.yaml` | Rendered `render_speak_yaml` for a regular 6-seat speaking round (seats 1-5 alive, seat 6 eliminated). |
| `vote-regular.yaml` | Rendered `render_vote_yaml` for the same regular voting round. |
| `speak-pk.yaml` | Rendered `render_speak_yaml` for a PK speaking round whose candidates are seats 2 and 4. |
| `vote-pk.yaml` | Rendered `render_vote_yaml` for the same PK voting round. |
| `graph-fixtures-check.mjs` | Asset-free Node check run from `package.json`'s `test:focused`. It re-parses the YAML above and verifies the committed graphs/params. |
| `../workflow-graphs.mjs` | Generated module exported as `workflowFixtures`; the only file the board tests import. Do not hand-edit. |

## Source of truth

- Script: `singlebox/agents/6bots_undercover_game_profile/referee/skills/undercover-game-referee/scripts/undercover.py`
- Generated from worktree `Avernet-ui` at `37a454d50` (branch `codex/issue-2608-jrpg-ui`).
- Script SHA-256 at generation time: `0a0369bff5ae26db5b2e75d712216ce25325cc7c320d69d317539779d0976897`
- Functions used: `render_speak_yaml`, `render_vote_yaml`, `public_panel_projection`, `is_pk`, `current_stage`, `speech_seats` (indirectly through the renderers), plus the module constants they read (`SPEECH_MAX_CHARS`, `VOTE_MAX_CHARS`, node timeouts).

The script is imported as a module with `importlib.util.spec_from_file_location`,
exactly like the script's own `tests/test_undercover_panel.py`. PyYAML is **not**
installed in the bundled Python 3 and is not needed: the generator and the Node
check each parse the small mapping-only YAML subset the renderers emit
(indentation maps, double-quoted scalars, inline flow sequences).

## Game state rendered

One state is rendered twice (regular and PK), so every fixture shares the same
roster and actor ids:

```python
seats = [
    {"seat": 1, "kind": "human", "display": "玩家", "bot_uuid": None, "binding": None, "word": ..., "role": "civilian", "alive": True},
    {"seat": 2, "kind": "bot", "display": "小甲", "bot_uuid": "bot-a", "binding": "seat2", "word": ..., "role": "civilian", "alive": True},
    {"seat": 3, "kind": "bot", "display": "小乙", "bot_uuid": "bot-b", "binding": "seat3", "word": ..., "role": "undercover", "alive": True},
    {"seat": 4, "kind": "bot", "display": "小丙", "bot_uuid": "bot-c", "binding": "seat4", "word": ..., "role": "civilian", "alive": True},
    {"seat": 5, "kind": "bot", "display": "小丁", "bot_uuid": "bot-d", "binding": "seat5", "word": ..., "role": "civilian", "alive": True},
    {"seat": 6, "kind": "bot", "display": "小戊", "bot_uuid": "bot-e", "binding": "seat6", "word": ..., "role": "civilian", "alive": False},
]
# fixed ids: human-1 / referee-1, group undercover-group-1, session undercover-session-1, round 1
# regular round: order [1, 2, 3, 4, 5], renders {"speak": 1, "vote": 1}; no tallied speeches or votes.
# PK stage:     order [2, 4] (seats 2 and 4 pass to PK), same empty speech/vote maps.
```

The eliminated seat 6 produces no node, which exercises the "dead player does
not act" topology. The human (seat 1) is alive, so the regular speak/vote YAML
contains a `human_input` node and the projection carries `currentAction`.
PK candidates are seats 2 and 4, i.e. **bot-a** and **bot-c**; this is the
`current_stage(state)["order"]` that the script itself reads.

Words and roles are throw-away dummy values. They are scrubbed before commit —
see below.

## Scrubbing

`generate_fixtures.py` removes every `instruction: |` block from the rendered
YAML before writing it. Those bodies are the only place the script puts the
private seat word (and, for human nodes, the `UNDERCOVER_UI_CONTEXT_V1` JSON
whose `word` field is the private word). Nothing else is edited: node ids,
`kind`, `display_name`, `assignee`, `node_timeout_ms`, `final_output`, and
`transitions.complete.targets` stay exactly as rendered. As a belt-and-braces
check the generator also replaces the exact word strings and fails if any word
or `instruction` text survives.

Consequences for reviewers:

- The committed YAML is topology provenance, not a byte-for-byte copy of what
  the referee submits; the omitted bodies are instruction text, not topology.
- No committed file contains a `word`/`role` value, `instruction`, or
  `artifact_text`.
- `graph-fixtures-check.mjs` asserts the scrub on every run (no `instruction:`,
  `word:`, `role:`, `fixture-word`/`dummy-word` marker in the YAML or module).

## YAML → API graph derivation

For each topology the generator parses the scrubbed YAML and builds:

- `graph.nodes`: one entry per YAML node, preserving YAML order:
  `node_id`, `display_name`, `kind` (`human_input` / `bot_task`), `assignee`
  when the YAML has one (`{type: "bot_binding", binding: "seat2"}`), and
  `final_output`. `status`/`attempt`/timestamps are the **test scenario**; the
  node identity comes from YAML.
- `graph.edges`: for every node, every
  `transitions.complete.targets` entry, as
  `{source, outcome: "complete", target}`. This gives the real serial chain in
  speak (`speak_N -> later speak_M` and `-> collect`) and the real fan-out in
  vote (`vote_start -> vote_N`, `vote_N -> tally`, never `vote_N -> vote_M`).
- `graph.definition`: `{id, version: 1, name, graph_mode: "acyclic",
  initial_nodes}` where `name` is the YAML `name:` line and `initial_nodes` is
  the single node with in-degree 0 (the first living speaker, or `vote_start`).
- `graph.run`: API-shaped `StateMachineRun` (`run_id`, `definition_id`,
  `definition_version`, `group_id`, `group_version`, `session_id`, `status`,
  `input`, `created_at`, `updated_at`; `completed_at` for the failed run) with
  fixed timestamps `1704067200000 + index * 1000` for determinism.

`params` is the **unnormalized** return of
`public_panel_projection(state, kind, attempt)`, with only `runId` replaced by
the fixture's fixed run id. It includes the real `phase`, `round`, `attempt`,
`host`, `seatOrder`, `turnOrder`, `players` (6 seats, seat 6
`alive:false/eliminated:true`), `nodeActorMap` (`collect`/`tally` map to the
referee; `vote_start` is intentionally not mapped, per
`references/runs.md`), `rules`, `currentViewerActorId`, `display`,
`currentAction`, and `voteCandidates`/`pkCandidates`/`openingAnnouncement`/
`resultFile` where the projection emits them.

## Fixtures

| Key | Provenance YAML | Graph run status | Scenario |
| --- | --- | --- | --- |
| `speakSequential` | `speak-regular.yaml` | running | `speak_1` completed, `speak_2` running, `speak_3` ready, `speak_4`/`speak_5`/`collect` pending. |
| `voteParallel` | `vote-regular.yaml` | running | `vote_start` completed, `vote_1`/`vote_2` completed, `vote_3` running, `vote_4`/`vote_5`/`tally` pending. |
| `voteTallyLast` | `vote-regular.yaml` | running | All `vote_N` completed, `tally` running. |
| `pkSpeak` | `speak-pk.yaml` | running | Serial PK speech over seats 2/4: `speak_2` completed, `speak_4` running, `collect` pending. |
| `pkVote` | `vote-pk.yaml` | running | `vote_start` completed, mixed vote states, `tally` pending; `voteCandidates` are exactly the PK candidates. |
| `failed` | `speak-regular.yaml` | failed | `speak_2` failed and carries `error: "PRIVATE_ERROR_SHOULD_NOT_RENDER"` so the board can prove it never renders `node.error`. |
| `retry` | `speak-regular.yaml` | running | `speak_2` `retry_scheduled`, `attempt: 2`; `params.attempt = 2`. |
| `missingEdges` | `speak-regular.yaml` | running | Nodes present, `graph.edges` omitted. |
| `staleRun` | `speak-regular.yaml` | running | `graph.run.run_id` (`run-workflow-speak-stale`) differs from `params.runId` (`run-workflow-speak`). |
| `unknownNode` | `vote-regular.yaml` | running | Real vote graph plus `vote_ghost`, which is absent from `params.nodeActorMap`. |

Run ids: `run-workflow-speak`, `run-workflow-vote`, `run-workflow-vote-tally`,
`run-workflow-pk-speak`, `run-workflow-pk-vote`, and per-scenario suffixes.

## Regenerating

```bash
PY=python3
$PY apps/bcs/assets/panel/test/undercover-game/fixtures/workflow/generate_fixtures.py
```

The generator walks up from its own directory to find the worktree root, so it
also works from another checkout as long as the referee script is present. It
is deterministic: rerunning produces identical YAML and `workflow-graphs.mjs`.

Check the committed result:

```bash
cd apps/bcs/assets/panel
node test/undercover-game/fixtures/workflow/graph-fixtures-check.mjs
# or: npm run test:focused
```

## Assumptions and limits

- **Statuses are scenarios, not observed runs.** The real graph endpoint supplies
  live statuses; these fixtures deliberately pin representative mixes
  (`completed`/`running`/`ready`/`pending`/`failed`/`retry_scheduled`) so the
  board can be tested deterministically without a live BCS.
- `run.input` is `{}` and `definition.id`/`definition_version` are synthetic
  fixed values; neither participates in the topology assertions.
- The `failed` fixture's node `error` is not part of the graph node API (node
  details expose it). It is an intentional privacy probe: the marker must never
  reach the DOM.
- `publicHistory`/`voteHistory` are empty because the rendered state has no
  tallied history yet. Adding history would require committing public speech
  text; the board's history rendering is covered elsewhere.
- `unknownNode` and `vote_ghost` are the only nodes not present in the rendered
  YAML. Every other fixture's node ids and edges are asserted to equal the YAML
  topology exactly.
