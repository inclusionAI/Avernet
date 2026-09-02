import assert from 'node:assert/strict';
import pkg from '../dist/index.umd.js';
const {
  fetchNodeDetail,
  fetchPendingHumanNodes,
  fetchRunGraph,
  fetchSessionMessages,
  normalizePanelParams,
  normalizeUndercoverGameViewModel,
  respondToHumanNode,
  serializeVoteContent,
} = pkg;

const calls = [];
const fixtures = new Map([
  ['/state-machine-runs/run-smoke/graph', { run: { run_id: 'run-smoke', status: 'completed' }, nodes: [{ node_id: 'speech-a', status: 'completed', attempt: 1 }] }],
  ['/state-machine-runs/run-smoke/pending-human-nodes', []],
  ['/sessions/session-smoke/messages?include_pending=true', [{ id: 'public-1', sequence: 1, content: 'public clue', metadata: { state_machine: { event: 'output', run_id: 'run-smoke', node_id: 'speech-a', attempt: 1 } } }]],
  ['/state-machine-runs/run-smoke/nodes/speech-a', { node: { node_id: 'speech-a', status: 'completed', attempt: 1 } }],
  ['/state-machine-runs/run-smoke/nodes/vote-human/respond', { accepted: true }],
]);
const fetchImpl = async (url, init = {}) => {
  const parsed = new URL(url, 'http://local.test');
  const key = `${parsed.pathname.replace('/api/v1/collaboration', '')}${parsed.search}`;
  calls.push({ key, init });
  const body = fixtures.get(key);
  return new Response(JSON.stringify({ code: 20000, message: 'OK', data: body }), { status: 200, headers: { 'content-type': 'application/json' } });
};
const base = '/api/v1/collaboration';
const params = normalizePanelParams({
  runId: 'run-smoke', groupId: 'group-smoke', sessionId: 'session-smoke', phase: 'reveal', round: 1,
  host: { actorId: 'host', displayName: 'Host' }, seatOrder: ['a'], players: [{ actorId: 'a', displayName: 'A' }],
  nodeActorMap: { 'speech-a': 'a' },
});
const [graph, pending, messages, detail] = await Promise.all([
  fetchRunGraph(base, params.runId, undefined, fetchImpl),
  fetchPendingHumanNodes(base, params.runId, undefined, fetchImpl),
  fetchSessionMessages(base, params.sessionId, undefined, fetchImpl),
  fetchNodeDetail(base, params.runId, 'speech-a', undefined, fetchImpl),
]);
assert.equal(graph.run.status, 'completed');
assert.equal(pending.length, 0);
assert.equal(messages[0].content, 'public clue');
assert.equal(detail.node.node_id, 'speech-a');
const model = normalizeUndercoverGameViewModel(params, graph, pending, messages);
assert.equal(model.actors.find((actor) => actor.actor.actorId === 'a').latestOutput.text, 'public clue');
assert.equal(model.terminal, true);
await respondToHumanNode(base, 'run-smoke', 'vote-human', serializeVoteContent('a'), undefined, fetchImpl);
assert.deepEqual(calls.at(-1).init.body, JSON.stringify({ content: '{"kind":"vote","target_actor_id":"a"}' }));
assert.equal(calls.some((call) => call.key.includes('include_pending=true')), true);
console.log('Integration smoke fixtures passed: graph, pending input, public messages, detail, vote payload, terminal stop data.');
