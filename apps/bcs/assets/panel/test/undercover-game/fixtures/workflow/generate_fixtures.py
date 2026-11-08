#!/usr/bin/env python3
"""Regenerate the committed workflow fixtures from the real referee script.

This script is provenance, not a test. It imports
``singlebox/agents/6bots_undercover_game_profile/referee/skills/undercover-game-referee/scripts/undercover.py``
by path (the same way that script's own ``tests/test_undercover_panel.py`` does),
renders the real ``render_speak_yaml`` / ``render_vote_yaml`` output for a fixed
6-seat game state, strips the private instruction bodies (they carry the seat
word and the human UI-context word), and re-emits:

  * ``speak-regular.yaml``, ``vote-regular.yaml``, ``speak-pk.yaml``, ``vote-pk.yaml``
  * ``../workflow-graphs.mjs`` (API-shaped graph + panel params per scenario)

Run with the repository Python 3:

  python3 apps/bcs/assets/panel/test/undercover-game/fixtures/workflow/generate_fixtures.py

PyYAML is not required: the emitted YAML is parsed with a tiny deterministic
parser for the exact subset produced by the referee script (indentation maps,
quoted scalars, inline flow sequences). The Node check in this directory
re-parses the committed YAML independently.
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUTPUT_MJS = HERE.parent / "workflow-graphs.mjs"

# Fixed source-of-truth script, resolved from this worktree.
SCRIPT_REL = Path(
    "singlebox/agents/6bots_undercover_game_profile/referee/skills/"
    "undercover-game-referee/scripts/undercover.py"
)


def find_repo_root(start: Path) -> Path:
    for candidate in [start, *start.parents]:
        if (candidate / SCRIPT_REL).is_file():
            return candidate
    raise SystemExit(f"referee script not found above {start}")


def load_referee():
    script = find_repo_root(HERE) / SCRIPT_REL
    spec = importlib.util.spec_from_file_location("undercover_referee", script)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot import {script}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, script


# ---------------------------------------------------------------------------
# Fixed game state used for every rendered YAML.
# ---------------------------------------------------------------------------
# Seats: 1 human alive, 2-5 bots alive, 6 bot eliminated (has no node).
# PK candidates: seats 2 and 4 (bot-a, bot-c) in that order.
# Words/roles are throw-away dummy values; they never reach a committed file.

WORDS = {
    1: "fixture-word-human",
    2: "fixture-word-two",
    3: "fixture-word-three",
    4: "fixture-word-four",
    5: "fixture-word-five",
    6: "fixture-word-six",
}

SEATS = [
    {"seat": 1, "kind": "human", "display": "玩家", "bot_uuid": None, "binding": None,
     "word": WORDS[1], "role": "civilian", "alive": True},
    {"seat": 2, "kind": "bot", "display": "小甲", "bot_uuid": "bot-a", "binding": "seat2",
     "word": WORDS[2], "role": "civilian", "alive": True},
    {"seat": 3, "kind": "bot", "display": "小乙", "bot_uuid": "bot-b", "binding": "seat3",
     "word": WORDS[3], "role": "undercover", "alive": True},
    {"seat": 4, "kind": "bot", "display": "小丙", "bot_uuid": "bot-c", "binding": "seat4",
     "word": WORDS[4], "role": "civilian", "alive": True},
    {"seat": 5, "kind": "bot", "display": "小丁", "bot_uuid": "bot-d", "binding": "seat5",
     "word": WORDS[5], "role": "civilian", "alive": True},
    {"seat": 6, "kind": "bot", "display": "小戊", "bot_uuid": "bot-e", "binding": "seat6",
     "word": WORDS[6], "role": "civilian", "alive": False},
]

GROUP_ID = "undercover-group-1"
SESSION_ID = "undercover-session-1"
HUMAN_ACTOR_ID = "human-1"
REFEREE_ACTOR_ID = "referee-1"
PK_ORDER = [2, 4]

# Fixed clock: 2024-01-01T00:00:00Z, +1s per node in definition order.
TS = 1_704_067_200_000


def make_state(pk: bool) -> dict:
    regular = {
        "round": 1,
        "order": [1, 2, 3, 4, 5],
        "speeches": {},
        "votes": {},
        "counts": {},
        "renders": {},
    }
    if pk:
        regular["pk"] = {
            "order": list(PK_ORDER),
            "speeches": {},
            "votes": {},
            "counts": {},
            "renders": {},
        }
    return {
        "session_id": SESSION_ID,
        "group_id": GROUP_ID,
        "human_actor_id": HUMAN_ACTOR_ID,
        "referee_uuid": REFEREE_ACTOR_ID,
        "phase": "AWAIT_START",
        "config": {"max_rounds": 6},
        "words": {"civilian": WORDS[1], "undercover": WORDS[3]},
        "round": 1,
        "rounds": [regular],
        "seats": [dict(seat) for seat in SEATS],
    }


# ---------------------------------------------------------------------------
# YAML rendering + scrub + minimal deterministic parse.
# ---------------------------------------------------------------------------

INSTRUCTION_HEADER = re.compile(r"^(\s*)instruction:\s*\|\s*$")


def strip_instruction_blocks(text: str) -> str:
    """Remove every ``instruction: |`` block, keeping the rest byte-identical.

    The script puts the private seat word (and, for human nodes, the private
    UI-context JSON with the word) inside instruction bodies. Node ids, kinds,
    assignees, timeouts, final_output flags and transitions are untouched.
    """
    lines = text.splitlines()
    kept: list[str] = []
    index = 0
    while index < len(lines):
        header = INSTRUCTION_HEADER.match(lines[index])
        if header is None:
            kept.append(lines[index])
            index += 1
            continue
        header_indent = len(header.group(1))
        index += 1
        while index < len(lines):
            body = lines[index]
            body_indent = len(body) - len(body.lstrip(" "))
            if body.strip() == "" or body_indent > header_indent:
                index += 1
                continue
            break
    return "\n".join(kept) + "\n"


def scrub_words(text: str) -> str:
    for word in WORDS.values():
        text = text.replace(word, "SCRUBBED_WORD")
    return text


def parse_scalar(raw: str):
    raw = raw.strip()
    if raw.startswith('"') and raw.endswith('"'):
        return json.loads(raw)
    if raw.startswith("[") and raw.endswith("]"):
        inner = raw[1:-1].strip()
        return [] if inner == "" else [parse_scalar(part) for part in inner.split(",")]
    if raw in ("true", "false"):
        return raw == "true"
    if re.fullmatch(r"-?\d+", raw):
        return int(raw)
    return raw


def parse_yaml_subset(text: str) -> dict:
    """Parse the deterministic mapping-only YAML the referee script emits."""
    root: dict = {}
    stack: list[tuple[int, dict]] = [(-1, root)]
    for raw_line in text.splitlines():
        if raw_line.strip() == "":
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        content = raw_line.strip()
        while len(stack) > 1 and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]
        key, separator, rest = content.partition(":")
        if separator == "":
            raise ValueError(f"unsupported YAML line: {raw_line!r}")
        key, rest = key.strip(), rest.strip()
        if rest == "":
            child: dict = {}
            parent[key] = child
            stack.append((indent, child))
        else:
            parent[key] = parse_scalar(rest)
    return root


def topology_from_yaml(text: str) -> dict:
    document = parse_yaml_subset(text)
    state_machine = document["runtime"]["state_machine"]
    nodes: dict[str, dict] = {}
    for node_id, node in state_machine["nodes"].items():
        transitions = node.get("transitions", {}).get("complete", {})
        nodes[node_id] = {
            "kind": node["kind"],
            "display_name": node["display_name"],
            "assignee": node.get("assignee"),
            "final_output": bool(node.get("final_output", False)),
            "targets": list(transitions.get("targets", [])),
        }
    return {
        "name": document["name"],
        "description": document["metadata"]["description"],
        "graph_mode": state_machine["graph_mode"],
        "nodes": nodes,
    }


# ---------------------------------------------------------------------------
# Scenario table. Statuses/attempts are the test scenarios layered on the real
# topology; every node id and every edge still comes from the rendered YAML.
# ---------------------------------------------------------------------------

SCENARIOS = [
    {
        "key": "speakSequential",
        "yaml": "speak-regular.yaml",
        "topology": "speak-regular",
        "runId": "run-workflow-speak",
        "definitionId": "undercover-speak-regular",
        "runStatus": "running",
        "statuses": {"speak_1": "completed", "speak_2": "running", "speak_3": "ready",
                     "speak_4": "pending", "speak_5": "pending", "collect": "pending"},
        "attempts": {},
        "params": {"state": "regular", "kind": "speak", "attempt": 1},
    },
    {
        "key": "voteParallel",
        "yaml": "vote-regular.yaml",
        "topology": "vote-regular",
        "runId": "run-workflow-vote",
        "definitionId": "undercover-vote-regular",
        "runStatus": "running",
        "statuses": {"vote_start": "completed", "vote_1": "completed", "vote_2": "completed",
                     "vote_3": "running", "vote_4": "pending", "vote_5": "pending",
                     "tally": "pending"},
        "attempts": {},
        "params": {"state": "regular", "kind": "vote", "attempt": 1},
    },
    {
        "key": "voteTallyLast",
        "yaml": "vote-regular.yaml",
        "topology": "vote-regular",
        "runId": "run-workflow-vote-tally",
        "definitionId": "undercover-vote-regular",
        "runStatus": "running",
        "statuses": {"vote_start": "completed", "vote_1": "completed", "vote_2": "completed",
                     "vote_3": "completed", "vote_4": "completed", "vote_5": "completed",
                     "tally": "running"},
        "attempts": {},
        "params": {"state": "regular", "kind": "vote", "attempt": 1},
    },
    {
        "key": "pkSpeak",
        "yaml": "speak-pk.yaml",
        "topology": "speak-pk",
        "runId": "run-workflow-pk-speak",
        "definitionId": "undercover-speak-pk",
        "runStatus": "running",
        "statuses": {"speak_2": "completed", "speak_4": "running", "collect": "pending"},
        "attempts": {},
        "params": {"state": "pk", "kind": "speak", "attempt": 1},
    },
    {
        "key": "pkVote",
        "yaml": "vote-pk.yaml",
        "topology": "vote-pk",
        "runId": "run-workflow-pk-vote",
        "definitionId": "undercover-vote-pk",
        "runStatus": "running",
        "statuses": {"vote_start": "completed", "vote_1": "completed", "vote_2": "running",
                     "vote_3": "completed", "vote_4": "pending", "vote_5": "pending",
                     "tally": "pending"},
        "attempts": {},
        "params": {"state": "pk", "kind": "vote", "attempt": 1},
    },
    {
        "key": "failed",
        "yaml": "speak-regular.yaml",
        "topology": "speak-regular",
        "runId": "run-workflow-speak-failed",
        "definitionId": "undercover-speak-regular",
        "runStatus": "failed",
        "statuses": {"speak_1": "completed", "speak_2": "failed", "speak_3": "pending",
                     "speak_4": "pending", "speak_5": "pending", "collect": "pending"},
        "attempts": {},
        "errors": {"speak_2": "PRIVATE_ERROR_SHOULD_NOT_RENDER"},
        "params": {"state": "regular", "kind": "speak", "attempt": 1},
    },
    {
        "key": "retry",
        "yaml": "speak-regular.yaml",
        "topology": "speak-regular",
        "runId": "run-workflow-speak-retry",
        "definitionId": "undercover-speak-regular",
        "runStatus": "running",
        "statuses": {"speak_1": "completed", "speak_2": "retry_scheduled",
                     "speak_3": "pending", "speak_4": "pending", "speak_5": "pending",
                     "collect": "pending"},
        "attempts": {"speak_2": 2},
        "params": {"state": "regular", "kind": "speak", "attempt": 2},
    },
    {
        "key": "missingEdges",
        "yaml": "speak-regular.yaml",
        "topology": "speak-regular",
        "runId": "run-workflow-speak-missing-edges",
        "definitionId": "undercover-speak-regular",
        "runStatus": "running",
        "statuses": {"speak_1": "completed", "speak_2": "running", "speak_3": "pending",
                     "speak_4": "pending", "speak_5": "pending", "collect": "pending"},
        "attempts": {},
        "omitEdges": True,
        "params": {"state": "regular", "kind": "speak", "attempt": 1},
    },
    {
        "key": "staleRun",
        "yaml": "speak-regular.yaml",
        "topology": "speak-regular",
        "runId": "run-workflow-speak-stale",
        "paramsRunId": "run-workflow-speak",
        "definitionId": "undercover-speak-regular",
        "runStatus": "running",
        "statuses": {"speak_1": "completed", "speak_2": "running", "speak_3": "ready",
                     "speak_4": "pending", "speak_5": "pending", "collect": "pending"},
        "attempts": {},
        "params": {"state": "regular", "kind": "speak", "attempt": 1},
    },
    {
        "key": "unknownNode",
        "yaml": "vote-regular.yaml",
        "topology": "vote-regular",
        "runId": "run-workflow-vote-unknown-node",
        "definitionId": "undercover-vote-regular",
        "runStatus": "running",
        "statuses": {"vote_start": "completed", "vote_1": "completed", "vote_2": "completed",
                     "vote_3": "running", "vote_4": "pending", "vote_5": "pending",
                     "tally": "pending"},
        "attempts": {},
        "extraNodes": [{"node_id": "vote_ghost", "display_name": "幽灵投票",
                        "kind": "bot_task", "final_output": False, "status": "pending",
                        "attempt": 1}],
        "params": {"state": "regular", "kind": "vote", "attempt": 1},
    },
]


# ---------------------------------------------------------------------------
# Build fixtures.
# ---------------------------------------------------------------------------

def build_node(node_id: str, node: dict, index: int, status: str, attempt: int,
               error: str | None) -> dict:
    graph_node: dict = {
        "node_id": node_id,
        "display_name": node["display_name"],
        "kind": node["kind"],
    }
    if node["assignee"] is not None:
        graph_node["assignee"] = node["assignee"]
    graph_node["final_output"] = node["final_output"]
    graph_node["status"] = status
    graph_node["attempt"] = attempt
    started_at = TS + index * 1000
    if status in ("completed", "running", "failed", "retry_scheduled"):
        graph_node["started_at"] = started_at
    if status in ("completed", "failed"):
        graph_node["completed_at"] = started_at + 400
    if error is not None:
        graph_node["error"] = error
    return graph_node


def build_graph(module, scenario: dict, topologies: dict, params: dict) -> dict:
    topology = topologies[scenario["topology"]]
    nodes = list(topology["nodes"].items())
    statuses = scenario["statuses"]
    attempts = scenario["attempts"]
    errors = scenario.get("errors", {})
    graph_nodes = []
    for index, (node_id, node) in enumerate(nodes):
        if node_id not in statuses:
            raise SystemExit(f"{scenario['key']}: no status for node {node_id}")
        graph_nodes.append(build_node(node_id, node, index, statuses[node_id],
                                      attempts.get(node_id, 1), errors.get(node_id)))
    graph_nodes.extend(scenario.get("extraNodes", []))

    incoming = {target for _, node in nodes for target in node["targets"]}
    initial_nodes = [node_id for node_id, _ in nodes if node_id not in incoming]
    run: dict = {
        "run_id": scenario["runId"],
        "definition_id": scenario["definitionId"],
        "definition_version": 1,
        "group_id": GROUP_ID,
        "group_version": 1,
        "session_id": SESSION_ID,
        "status": scenario["runStatus"],
        "input": {},
        "created_at": TS,
        "updated_at": TS + 6000,
    }
    if scenario["runStatus"] == "failed":
        run["completed_at"] = TS + 6000
    graph: dict = {
        "run": run,
        "definition": {
            "id": scenario["definitionId"],
            "version": 1,
            "name": topology["name"],
            "graph_mode": topology["graph_mode"],
            "initial_nodes": initial_nodes,
        },
        "nodes": graph_nodes,
    }
    if not scenario.get("omitEdges"):
        graph["edges"] = [
            {"source": node_id, "outcome": "complete", "target": target}
            for node_id, node in nodes
            for target in node["targets"]
        ]
    return graph


def build_params(module, scenario: dict, states: dict) -> dict:
    spec = scenario["params"]
    state = states[spec["state"]]
    params = module.public_panel_projection(state, spec["kind"], spec["attempt"])
    params["runId"] = scenario.get("paramsRunId", scenario["runId"])
    return params


# ---------------------------------------------------------------------------
# Minimal JS emitter: inline small objects, multi-line for the outer params.
# ---------------------------------------------------------------------------

IDENTIFIER = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")


def js_key(key: str) -> str:
    return key if IDENTIFIER.match(key) else json.dumps(key, ensure_ascii=False)


def js_scalar(value) -> str:
    return json.dumps(value, ensure_ascii=False)


def js_inline(value) -> str | None:
    """One-line rendering; None when the value needs a multi-line layout."""
    if isinstance(value, dict):
        if not value:
            return "{}"
        parts = []
        for key, item in value.items():
            if isinstance(item, list):
                return None
            rendered = js_inline(item) if isinstance(item, dict) else js_scalar(item)
            if rendered is None:
                return None
            parts.append(f"{js_key(key)}: {rendered}")
        return "{ " + ", ".join(parts) + " }"
    if isinstance(value, list):
        if not value:
            return "[]"
        if any(isinstance(item, (dict, list)) for item in value):
            return None
        return "[" + ", ".join(js_scalar(item) for item in value) + "]"
    return js_scalar(value)


def js_pretty(value, indent: int = 0) -> str:
    pad = "  " * indent
    inline = js_inline(value)
    if inline is not None and len(inline) <= 220:
        return inline
    if isinstance(value, dict):
        lines = ["{"]
        for key, item in value.items():
            rendered = js_pretty(item, indent + 1)
            lines.append(f"{pad}  {js_key(key)}: {rendered},")
        lines.append(pad + "}")
        return "\n".join(lines)
    if isinstance(value, list):
        lines = ["["]
        for item in value:
            rendered = js_inline(item)
            if rendered is None:
                rendered = js_pretty(item, indent + 1)
            lines.append(f"{pad}  {rendered},")
        lines.append(pad + "]")
        return "\n".join(lines)
    return js_scalar(value)


def render_mjs(fixtures: list[dict], source_script: str) -> str:
    lines = [
        "// AUTO-GENERATED by ./workflow/generate_fixtures.py -- do not hand-edit.",
        "//",
        "// Source of truth: " + source_script,
        "//   render_speak_yaml / render_vote_yaml / public_panel_projection",
        "// YAML provenance lives next to this file under ./workflow/.",
        "// Graphs are API-shaped (run/nodes/edges); params are unnormalized",
        "// public_panel_projection output with runId pinned to the graph run.",
        "",
        "export const workflowFixtures = {",
    ]
    for fixture in fixtures:
        lines.append(f"  {fixture['key']}: {{")
        lines.append("    graph: " + js_pretty(fixture["graph"], 2) + ",")
        lines.append("    params: " + js_pretty(fixture["params"], 2) + ",")
        lines.append("  },")
    lines.append("};")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main.
# ---------------------------------------------------------------------------

def main() -> None:
    module, source_script = load_referee()
    states = {"regular": make_state(False), "pk": make_state(True)}

    rendered = {
        "speak-regular": module.render_speak_yaml(states["regular"])[0],
        "vote-regular": module.render_vote_yaml(states["regular"])[0],
        "speak-pk": module.render_speak_yaml(states["pk"])[0],
        "vote-pk": module.render_vote_yaml(states["pk"])[0],
    }

    scrubbed: dict[str, str] = {}
    topologies: dict[str, dict] = {}
    for name, raw in rendered.items():
        text = scrub_words(strip_instruction_blocks(raw))
        if "instruction" in text:
            raise SystemExit(f"{name}: instruction block survived the scrub")
        for word in WORDS.values():
            if word in text:
                raise SystemExit(f"{name}: private word survived the scrub")
        scrubbed[name] = text
        topologies[name] = topology_from_yaml(text)

    for name, text in scrubbed.items():
        (HERE / f"{name}.yaml").write_text(text, encoding="utf-8")

    fixtures = []
    for scenario in SCENARIOS:
        params = build_params(module, scenario, states)
        graph = build_graph(module, scenario, topologies, params)
        fixtures.append({"key": scenario["key"], "graph": graph, "params": params})

    source_label = SCRIPT_REL.as_posix()
    OUTPUT_MJS.write_text(render_mjs(fixtures, source_label), encoding="utf-8")

    print("wrote:")
    for name in scrubbed:
        count = len(topologies[name]["nodes"])
        edges = sum(len(node["targets"]) for node in topologies[name]["nodes"].values())
        print(f"  workflow/{name}.yaml ({count} nodes, {edges} edges)")
    print(f"  workflow-graphs.mjs ({len(fixtures)} fixtures)")
    for fixture in fixtures:
        node_ids = [node["node_id"] for node in fixture["graph"]["nodes"]]
        edges = fixture["graph"].get("edges")
        print(f"  {fixture['key']}: nodes={node_ids} edges={len(edges) if edges is not None else 'undefined'}")


if __name__ == "__main__":
    main()
