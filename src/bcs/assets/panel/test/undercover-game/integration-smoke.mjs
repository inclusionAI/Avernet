import assert from 'node:assert/strict';
import pkg from '../../dist/index.umd.js';
const {
  fetchNodeDetail,
  fetchPendingHumanNodes,
  fetchRunGraph,
  publicEventFromNodeDetail,
  normalizePanelParams,
  normalizeUndercoverGameViewModel,
  respondToHumanNode,
  serializeVoteContent,
} = pkg;

// Exercise local raw responses and host-injected gateway envelopes through
// normalized game params, including the human-response submission path.
for (const environment of [
  { expectedBase: '/bcnproxy', props: {}, enveloped: false },
  { expectedBase: '/api/v1/collaboration', props: { apiBaseUrl: '/api/v1/collaboration' }, enveloped: true },
]) {
const calls = [];
const fixtures = new Map([
  ['/state-machine-runs/run-smoke/graph', { run: { run_id: 'run-smoke', status: 'completed' }, nodes: [{ node_id: 'speech-a', status: 'completed', attempt: 1 }] }],
  ['/state-machine-runs/run-smoke/pending-human-nodes', []],
  ['/state-machine-runs/run-smoke/nodes/speech-a', { node: { node_id: 'speech-a', status: 'completed', attempt: 1, artifact_text: 'public clue' } }],
  ['/state-machine-runs/run-smoke/nodes/vote-human/respond', { accepted: true }],
]);
const fetchImpl = async (url, init = {}) => {
  const parsed = new URL(url, 'http://local.test');
  assert.ok(parsed.pathname.startsWith(`${environment.expectedBase}/`), url);
  const key = `${parsed.pathname.slice(environment.expectedBase.length)}${parsed.search}`;
  calls.push({ key, init });
  const body = fixtures.get(key);
  assert.ok(fixtures.has(key), key);
  return new Response(JSON.stringify(environment.enveloped ? { code: 20000, message: 'OK', data: body } : body), { status: 200, headers: { 'content-type': 'application/json' } });
};
const params = normalizePanelParams({
  runId: 'run-smoke', groupId: 'group-smoke', sessionId: 'session-smoke', phase: 'reveal', round: 1,
  host: { actorId: 'host', displayName: 'Host' }, seatOrder: ['a'], players: [{ actorId: 'a', displayName: 'A' }],
  nodeActorMap: { 'speech-a': 'a' },
  ...environment.props,
});
const base = params.apiBaseUrl;
assert.equal(base, environment.expectedBase);
const [graph, pending, detail] = await Promise.all([
  fetchRunGraph(base, params.runId, undefined, fetchImpl),
  fetchPendingHumanNodes(base, params.runId, undefined, fetchImpl),
  fetchNodeDetail(base, params.runId, 'speech-a', undefined, fetchImpl),
]);
assert.equal(graph.run.status, 'completed');
assert.equal(pending.length, 0);
assert.equal(detail.node.node_id, 'speech-a');
const model = normalizeUndercoverGameViewModel(params, graph, pending, [], [publicEventFromNodeDetail(detail, params)]);
assert.equal(model.actors.find((actor) => actor.actor.actorId === 'a').latestOutput.text, 'public clue');
assert.equal(model.terminal, true);
await respondToHumanNode(base, 'run-smoke', 'vote-human', serializeVoteContent('a'), undefined, fetchImpl);
assert.deepEqual(calls.at(-1).init.body, JSON.stringify({ content: '{"kind":"vote","target_actor_id":"a"}' }));
assert.equal(calls.some((call) => call.key.includes('/sessions/')), false);
}
console.log('Local and deployed integration smoke fixtures passed: graph, pending input, node artifacts, detail, vote payload, terminal stop data.');
