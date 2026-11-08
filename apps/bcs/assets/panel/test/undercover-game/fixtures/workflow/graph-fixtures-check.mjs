#!/usr/bin/env node
// Verifies the committed workflow fixtures against their real referee-script
// YAML provenance. Pure Node, no dependencies.
//
// The YAML files record the topology produced by
// singlebox/.../undercover-game-referee/scripts/undercover.py
// (render_speak_yaml / render_vote_yaml). The graph objects in
// ../workflow-graphs.mjs must have exactly the node ids, node kinds and edge
// targets from those files; params must match the public_panel_projection
// contract and stay free of private words, instructions and raw vote targets.

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import { workflowFixtures } from '../workflow-graphs.mjs';

const FIXTURE_KEYS = [
  'speakSequential', 'voteParallel', 'voteTallyLast', 'pkSpeak', 'pkVote',
  'failed', 'retry', 'missingEdges', 'staleRun', 'unknownNode',
];
const PARAM_KEYS = [
  'runId', 'groupId', 'sessionId', 'gameSessionId', 'phase', 'round', 'attempt',
  'host', 'seatOrder', 'turnOrder', 'players', 'nodeActorMap', 'rules',
  'currentViewerActorId',
];
const FORBIDDEN_KEYS = new Set([
  'artifact_text', 'instruction', 'word', 'role', 'raw', 'raw_text', 'rawText',
  'target_actor_id', 'targetSeatNumber', 'voteTarget', 'privateContext',
]);
const ERROR_MARKER = 'PRIVATE_ERROR_SHOULD_NOT_RENDER';

function yamlPath(name) {
  return new URL(`./${name}`, import.meta.url);
}

// ---------------------------------------------------------------------------
// Minimal parser for the exact YAML subset the referee script emits: nested
// indentation maps, double-quoted scalars, inline flow sequences and scalars.
// Instruction bodies are removed before commit, so no block scalars appear.
// ---------------------------------------------------------------------------
function parseScalar(raw) {
  if (raw.startsWith('"') && raw.endsWith('"')) return JSON.parse(raw);
  if (raw.startsWith('[') && raw.endsWith(']')) {
    const inner = raw.slice(1, -1).trim();
    return inner ? inner.split(',').map((part) => parseScalar(part.trim())) : [];
  }
  if (raw === 'true' || raw === 'false') return raw === 'true';
  if (/^-?\d+$/.test(raw)) return Number(raw);
  return raw;
}

function parseYaml(text) {
  const root = {};
  const stack = [{ indent: -1, value: root }];
  for (const rawLine of text.split('\n')) {
    if (!rawLine.trim()) continue;
    const indent = rawLine.length - rawLine.trimStart().length;
    const content = rawLine.trim();
    while (stack.length > 1 && indent <= stack[stack.length - 1].indent) stack.pop();
    const parent = stack[stack.length - 1].value;
    const splitAt = content.indexOf(':');
    assert.ok(splitAt > 0, `unsupported YAML line: ${rawLine}`);
    const key = content.slice(0, splitAt).trim();
    const rest = content.slice(splitAt + 1).trim();
    if (rest === '') {
      parent[key] = {};
      stack.push({ indent, value: parent[key] });
    } else {
      parent[key] = parseScalar(rest);
    }
  }
  return root;
}

function loadTopology(fileName) {
  const document = parseYaml(readFileSync(yamlPath(fileName), 'utf8'));
  const stateMachine = document.runtime.state_machine;
  const nodes = Object.entries(stateMachine.nodes).map(([nodeId, node]) => ({
    node_id: nodeId,
    kind: node.kind,
    display_name: node.display_name,
    assignee: node.assignee ?? null,
    final_output: node.final_output === true,
    targets: node.transitions?.complete?.targets ?? [],
  }));
  return {
    file: fileName,
    name: document.name,
    participants: Object.keys(document.participants),
    graph_mode: stateMachine.graph_mode,
    nodes,
  };
}

function yamlEdges(topology) {
  return topology.nodes.flatMap((node) => node.targets.map((target) => ({
    source: node.node_id,
    outcome: 'complete',
    target,
  })));
}

function nodeIds(topology) {
  return topology.nodes.map((node) => node.node_id);
}

// ---------------------------------------------------------------------------
// Small invariant helpers.
// ---------------------------------------------------------------------------
function walkKeys(value, visitor, path = '$') {
  if (Array.isArray(value)) {
    value.forEach((item, index) => walkKeys(item, visitor, `${path}[${index}]`));
    return;
  }
  if (!value || typeof value !== 'object') return;
  for (const [key, item] of Object.entries(value)) {
    visitor(key, item, `${path}.${key}`);
    walkKeys(item, visitor, `${path}.${key}`);
  }
}

function assertShape(fixtureKey, fixture) {
  assert.ok(fixture && typeof fixture === 'object', `${fixtureKey}: fixture must be an object`);
  assert.deepEqual(Object.keys(fixture), ['graph', 'params'], `${fixtureKey}: expected { graph, params }`);
  const { graph, params } = fixture;
  assert.ok(graph && typeof graph === 'object', `${fixtureKey}: graph missing`);
  assert.ok(Array.isArray(graph.nodes), `${fixtureKey}: graph.nodes must be an array`);
  assert.ok(graph.run && typeof graph.run.run_id === 'string', `${fixtureKey}: graph.run.run_id missing`);
  for (const key of PARAM_KEYS) {
    assert.ok(params && params[key] !== undefined && params[key] !== null, `${fixtureKey}: params.${key} missing`);
  }
  assert.ok(Array.isArray(params.players) && params.players.length === 6, `${fixtureKey}: params.players must hold 6 seats`);
  assert.ok(Array.isArray(params.seatOrder) && params.seatOrder.length === 6, `${fixtureKey}: params.seatOrder must hold 6 seats`);
  assert.ok(Object.keys(params.nodeActorMap).length > 0, `${fixtureKey}: nodeActorMap must not be empty`);
  // Graphs carry no production-private fields except the deliberate failed error.
  walkKeys(graph.nodes, (key, _value, path) => {
    if (FORBIDDEN_KEYS.has(key)) assert.fail(`${fixtureKey}: forbidden graph node field ${key} at ${path}`);
    if (key === 'target') assert.fail(`${fixtureKey}: raw target field ${path} in graph nodes`);
  });
  walkKeys(params, (key, _value, path) => {
    if (FORBIDDEN_KEYS.has(key)) assert.fail(`${fixtureKey}: forbidden params field ${key} at ${path}`);
    if (key === 'target') assert.fail(`${fixtureKey}: raw target field ${path} in params`);
  });
}

function assertGraphTopology(fixtureKey, graph, topology, { expectEdges = true, extraNodeIds = [] } = {}) {
  const expectedIds = [...nodeIds(topology), ...extraNodeIds];
  assert.deepEqual(graph.nodes.map((node) => node.node_id), expectedIds,
    `${fixtureKey}: node ids must match ${topology.file} exactly`);
  for (const expected of topology.nodes) {
    const actual = graph.nodes.find((node) => node.node_id === expected.node_id);
    assert.equal(actual.display_name, expected.display_name, `${fixtureKey}: ${expected.node_id} display_name`);
    assert.equal(actual.kind, expected.kind, `${fixtureKey}: ${expected.node_id} kind`);
    assert.deepEqual(actual.assignee ?? null, expected.assignee, `${fixtureKey}: ${expected.node_id} assignee`);
    assert.equal(actual.final_output, expected.final_output, `${fixtureKey}: ${expected.node_id} final_output`);
  }
  if (expectEdges) {
    assert.deepEqual(graph.edges, yamlEdges(topology), `${fixtureKey}: edges must match ${topology.file} targets`);
  } else {
    assert.equal(graph.edges, undefined, `${fixtureKey}: edges must be undefined`);
  }
}

function assertSingleSink(fixtureKey, graph, sinkId, extraSinks = []) {
  const outgoing = new Set((graph.edges ?? []).map((edge) => edge.source));
  const sinks = graph.nodes.map((node) => node.node_id).filter((nodeId) => !outgoing.has(nodeId));
  assert.deepEqual(sinks, [sinkId, ...extraSinks], `${fixtureKey}: ${sinkId} must be the only real sink`);
  for (const nodeId of graph.nodes.map((node) => node.node_id)) {
    if (nodeId === sinkId || extraSinks.includes(nodeId)) continue;
    assert.ok(outgoing.has(nodeId), `${fixtureKey}: dangling non-sink node ${nodeId}`);
  }
}

function statusOf(graph, nodeId) {
  return graph.nodes.find((node) => node.node_id === nodeId)?.status;
}

function assertVoteEdges(fixtureKey, graph, { voteStart = 'vote_start', tally = 'tally', extraSinks = [] } = {}) {
  const voteNodes = graph.nodes.map((node) => node.node_id).filter((nodeId) => /^vote_\d+$/.test(nodeId));
  const edges = graph.edges ?? [];
  assert.deepEqual(
    edges.filter((edge) => !(edge.source === voteStart && voteNodes.includes(edge.target)) && !(voteNodes.includes(edge.source) && edge.target === tally)),
    [],
    `${fixtureKey}: vote edges must be ${voteStart}->vote_N plus vote_N->${tally}`,
  );
  for (const edge of edges) {
    assert.equal(voteNodes.includes(edge.source) && voteNodes.includes(edge.target), false,
      `${fixtureKey}: no vote_N -> vote_N edges`);
  }
  assert.deepEqual(new Set(edges.filter((edge) => edge.source === voteStart).map((edge) => edge.target)), new Set(voteNodes),
    `${fixtureKey}: ${voteStart} must fan out to every vote node`);
  assert.deepEqual(new Set(edges.filter((edge) => edge.target === tally).map((edge) => edge.source)), new Set(voteNodes),
    `${fixtureKey}: every vote node must reach ${tally}`);
  assertSingleSink(fixtureKey, graph, tally, extraSinks);
}

// ---------------------------------------------------------------------------
// Load provenance once.
// ---------------------------------------------------------------------------
const topologies = {
  speakRegular: loadTopology('speak-regular.yaml'),
  voteRegular: loadTopology('vote-regular.yaml'),
  speakPk: loadTopology('speak-pk.yaml'),
  votePk: loadTopology('vote-pk.yaml'),
};

// The committed YAML is scrubbed provenance: topology only, no instruction
// body, no word and no role. Assert that before trusting it.
for (const topology of Object.values(topologies)) {
  const text = readFileSync(yamlPath(topology.file), 'utf8');
  for (const forbidden of ['instruction:', 'word:', 'role:', 'artifact_text:', 'fixture-word', 'dummy-word', 'SCRUBBED_WORD']) {
    assert.equal(text.includes(forbidden), false, `${topology.file}: scrubbed YAML leaks ${forbidden}`);
  }
  const ids = new Set(nodeIds(topology));
  for (const node of topology.nodes) {
    assert.ok(node.kind, `${topology.file}: ${node.node_id} kind`);
    assert.ok(node.display_name, `${topology.file}: ${node.node_id} display_name`);
    for (const target of node.targets) assert.ok(ids.has(target), `${topology.file}: ${node.node_id} -> ${target} outside graph`);
  }
  const incoming = new Set(topology.nodes.flatMap((node) => node.targets));
  assert.deepEqual(nodeIds(topology).filter((nodeId) => !incoming.has(nodeId)), [topology.nodes[0].node_id],
    `${topology.file}: exactly one entry node`);
  assert.equal(topology.nodes[topology.nodes.length - 1].final_output, true,
    `${topology.file}: last node must be final_output`);
}

assert.deepEqual(Object.keys(workflowFixtures).sort(), [...FIXTURE_KEYS].sort(),
  'workflowFixtures must expose exactly the required keys');
for (const fixtureKey of FIXTURE_KEYS) assertShape(fixtureKey, workflowFixtures[fixtureKey]);

const {
  speakSequential, voteParallel, voteTallyLast, pkSpeak, pkVote,
  failed, retry, missingEdges, staleRun, unknownNode,
} = workflowFixtures;

// ---------------------------------------------------------------------------
// Real speak topology: serial chain, collect is the only sink.
// ---------------------------------------------------------------------------
for (const [fixtureKey, fixture] of Object.entries({ speakSequential, failed, retry, staleRun })) {
  assertGraphTopology(fixtureKey, fixture.graph, topologies.speakRegular);
  assertSingleSink(fixtureKey, fixture.graph, 'collect');
}
assertGraphTopology('missingEdges', missingEdges.graph, topologies.speakRegular, { expectEdges: false });
assert.equal(missingEdges.graph.edges, undefined, 'missingEdges: edges must be omitted');

assert.equal(statusOf(speakSequential.graph, 'speak_1'), 'completed', 'speakSequential: first speaker completed');
assert.equal(statusOf(speakSequential.graph, 'speak_2'), 'running', 'speakSequential: second speaker running');
assert.equal(statusOf(speakSequential.graph, 'speak_3'), 'ready', 'speakSequential: third speaker ready');
for (const nodeId of ['speak_4', 'speak_5', 'collect']) {
  assert.equal(statusOf(speakSequential.graph, nodeId), 'pending', `speakSequential: ${nodeId} pending`);
}
assert.equal(speakSequential.graph.run.status, 'running', 'speakSequential: run running');

// ---------------------------------------------------------------------------
// Real vote topology: vote_start fans out, vote_N never chain, tally is sink.
// ---------------------------------------------------------------------------
for (const [fixtureKey, fixture] of Object.entries({ voteParallel, voteTallyLast })) {
  assertGraphTopology(fixtureKey, fixture.graph, topologies.voteRegular);
  assertVoteEdges(fixtureKey, fixture.graph);
  assert.equal(fixture.graph.run.status, 'running', `${fixtureKey}: run running`);
}
// unknownNode layers one deliberate engine node on the real vote topology.
assertGraphTopology('unknownNode', unknownNode.graph, topologies.voteRegular, { extraNodeIds: ['vote_ghost'] });
assertVoteEdges('unknownNode', unknownNode.graph, { extraSinks: ['vote_ghost'] });
assert.equal(statusOf(voteParallel.graph, 'vote_start'), 'completed', 'voteParallel: vote_start completed');
assert.equal(statusOf(voteParallel.graph, 'vote_3'), 'running', 'voteParallel: one vote running');
assert.equal(statusOf(voteParallel.graph, 'vote_1'), 'completed', 'voteParallel: one vote completed');
assert.equal(statusOf(voteParallel.graph, 'vote_4'), 'pending', 'voteParallel: one vote pending');
assert.equal(statusOf(voteParallel.graph, 'tally'), 'pending', 'voteParallel: tally pending');
for (const nodeId of ['vote_1', 'vote_2', 'vote_3', 'vote_4', 'vote_5']) {
  assert.equal(statusOf(voteTallyLast.graph, nodeId), 'completed', `voteTallyLast: ${nodeId} completed`);
}
assert.equal(statusOf(voteTallyLast.graph, 'tally'), 'running', 'voteTallyLast: tally running');
const ghost = unknownNode.graph.nodes.find((node) => node.node_id === 'vote_ghost');
assert.ok(ghost, 'unknownNode: vote_ghost node present');
assert.equal(unknownNode.params.nodeActorMap.vote_ghost, undefined, 'unknownNode: vote_ghost absent from nodeActorMap');

// ---------------------------------------------------------------------------
// PK fixtures: only candidate speakers / candidate vote targets.
// ---------------------------------------------------------------------------
assertGraphTopology('pkSpeak', pkSpeak.graph, topologies.speakPk);
assertSingleSink('pkSpeak', pkSpeak.graph, 'collect');
assert.deepEqual(nodeIds(topologies.speakPk), ['speak_2', 'speak_4', 'collect'], 'speak-pk.yaml: only PK candidates speak');
assert.equal(topologies.speakPk.participants.includes('seat3'), false, 'speak-pk.yaml: seat 3 (non-PK) has no participant');
assert.equal(topologies.speakPk.participants.includes('seat5'), false, 'speak-pk.yaml: seat 5 (non-PK) has no participant');
assert.deepEqual(pkSpeak.params.pkCandidates, ['bot-a', 'bot-c'], 'pkSpeak: pkCandidates from projection');
for (const node of pkSpeak.graph.nodes.filter((item) => /^speak_\d+$/.test(item.node_id))) {
  assert.ok(pkSpeak.params.pkCandidates.includes(pkSpeak.params.nodeActorMap[node.node_id]),
    `pkSpeak: ${node.node_id} must map to a PK candidate`);
}

assertGraphTopology('pkVote', pkVote.graph, topologies.votePk);
assertVoteEdges('pkVote', pkVote.graph);
assert.deepEqual(pkVote.params.pkCandidates, ['bot-a', 'bot-c'], 'pkVote: pkCandidates from projection');
const pkVoteCandidateIds = pkVote.params.voteCandidates.map((candidate) => candidate.actorId);
assert.deepEqual(pkVoteCandidateIds, pkVote.params.pkCandidates, 'pkVote: voteCandidates are exactly PK candidates');
for (const candidate of pkVote.params.voteCandidates) {
  assert.ok(pkVote.params.pkCandidates.includes(candidate.actorId), `pkVote: ${candidate.actorId} not a PK candidate`);
  assert.equal(candidate.eligible, true, `pkVote: ${candidate.actorId} eligible`);
}
const livingVoterNodeIds = Object.keys(pkVote.params.nodeActorMap).filter((nodeId) => /^vote_\d+$/.test(nodeId));
assert.deepEqual(livingVoterNodeIds, ['vote_1', 'vote_2', 'vote_3', 'vote_4', 'vote_5'],
  'pkVote: all living players still vote');

// ---------------------------------------------------------------------------
// Failed / retry / stale / missing-edges scenarios.
// ---------------------------------------------------------------------------
const failedNodes = failed.graph.nodes.filter((node) => node.error !== undefined);
assert.deepEqual(failedNodes.map((node) => node.node_id), ['speak_2'], 'failed: only speak_2 carries error');
assert.equal(failedNodes[0].error, ERROR_MARKER, 'failed: error marker preserved for the board privacy test');
assert.equal(failedNodes[0].status, 'failed', 'failed: speak_2 status failed');
assert.equal(failed.graph.run.status, 'failed', 'failed: run status failed');
for (const [fixtureKey, fixture] of Object.entries(workflowFixtures)) {
  if (fixtureKey === 'failed') continue;
  walkKeys(fixture.graph, (key) => {
    if (key === 'error') assert.fail(`${fixtureKey}: unexpected error field; only failed keeps the privacy marker`);
  });
}
assert.equal(JSON.stringify(failed.graph).includes(ERROR_MARKER), true, 'failed: marker present in failed graph');
for (const fixtureKey of FIXTURE_KEYS) {
  if (fixtureKey === 'failed') continue;
  assert.equal(JSON.stringify(workflowFixtures[fixtureKey]).includes(ERROR_MARKER), false,
    `failed: marker must not leak into ${fixtureKey}`);
}

assert.equal(statusOf(retry.graph, 'speak_2'), 'retry_scheduled', 'retry: speak_2 retry_scheduled');
assert.equal(retry.graph.nodes.find((node) => node.node_id === 'speak_2').attempt, 2, 'retry: speak_2 attempt 2');
assert.equal(retry.params.attempt, 2, 'retry: params attempt 2');

assert.equal(missingEdges.graph.edges, undefined, 'missingEdges: graph.edges undefined while nodes exist');

assert.notEqual(staleRun.graph.run.run_id, staleRun.params.runId, 'staleRun: graph run_id must differ from params.runId');
for (const fixtureKey of FIXTURE_KEYS) {
  if (fixtureKey === 'staleRun') continue;
  assert.equal(workflowFixtures[fixtureKey].graph.run.run_id, workflowFixtures[fixtureKey].params.runId,
    `${fixtureKey}: graph.run.run_id must equal params.runId`);
}

// ---------------------------------------------------------------------------
// Params are the unnormalized projection: shared states must agree.
// ---------------------------------------------------------------------------
function paramsWithout(fixture, keys) {
  const copy = structuredClone(fixture.params);
  for (const key of keys) delete copy[key];
  return copy;
}
for (const fixtureKey of ['failed', 'retry', 'missingEdges', 'staleRun']) {
  assert.deepEqual(paramsWithout(workflowFixtures[fixtureKey], ['runId', 'attempt']),
    paramsWithout(speakSequential, ['runId', 'attempt']),
    `${fixtureKey}: params must be the same speak projection as speakSequential`);
}
assert.deepEqual(paramsWithout(unknownNode, ['runId']), paramsWithout(voteParallel, ['runId']),
  'unknownNode: params must be the same vote projection as voteParallel');
assert.deepEqual(paramsWithout(voteTallyLast, ['runId']), paramsWithout(voteParallel, ['runId']),
  'voteTallyLast: params must be the same vote projection as voteParallel');

// ---------------------------------------------------------------------------
// Forbidden content scan across every committed fixture file.
// ---------------------------------------------------------------------------
for (const fixtureKey of FIXTURE_KEYS) {
  walkKeys(workflowFixtures[fixtureKey], (key, _value, path) => {
    if (key === 'error' && fixtureKey !== 'failed') assert.fail(`${fixtureKey}: only failed may carry error (${path})`);
  });
}
const mjsText = readFileSync(new URL('../workflow-graphs.mjs', import.meta.url), 'utf8');
for (const forbidden of ['fixture-word', 'dummy-word', 'PRIVATE_ERROR_SHOULD_NOT_RENDER']) {
  const occurrences = mjsText.split(forbidden).length - 1;
  if (forbidden === ERROR_MARKER) assert.equal(occurrences, 1, 'failed: error marker must appear exactly once in the module');
  else assert.equal(occurrences, 0, `workflow-graphs.mjs leaks ${forbidden}`);
}

// ---------------------------------------------------------------------------
// Report.
// ---------------------------------------------------------------------------
console.log('graph-fixtures-check: PASS');
console.log(`  fixtures: ${FIXTURE_KEYS.length} (${FIXTURE_KEYS.join(', ')})`);
for (const topology of Object.values(topologies)) {
  console.log(`  yaml ${topology.file}: ${topology.nodes.length} nodes, ${yamlEdges(topology).length} edges, entry=${topology.nodes[0].node_id}`);
}
console.log(`  speakSequential: real serial graph ${nodeIds(topologies.speakRegular).join(', ')}; collect is the only sink`);
console.log(`  voteParallel: vote_start fans out to ${nodeIds(topologies.voteRegular).filter((nodeId) => /^vote_\d+$/.test(nodeId)).join(', ')} then tally; no vote-to-vote edges; sink=tally`);
console.log(`  voteTallyLast: same topology, all votes completed, tally running`);
console.log(`  pkSpeak: ${nodeIds(topologies.speakPk).join(' -> ')}; PK candidates bot-a, bot-c only`);
console.log(`  pkVote: vote_start -> 5 living voters -> tally; voteCandidates = PK candidates`);
console.log(`  failed/retry/missingEdges/staleRun/unknownNode: resilience scenarios on the real topologies`);
console.log(`  params: unnormalized public_panel_projection output; graph.run.run_id == params.runId except staleRun`);
console.log(`  scrub: committed YAML has no instruction body, word or role; error marker only on failed/speak_2`);
