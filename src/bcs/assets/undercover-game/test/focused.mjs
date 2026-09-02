import assert from 'node:assert/strict';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const api = require('../dist/index.umd.js');
const {
  ApiRequestError,
  eligibleVoteCandidates,
  getSeatCoordinates,
  mapPublicOutputEvents,
  normalizePanelParams,
  normalizeUndercoverGameViewModel,
  requestJson,
  serializeVoteContent,
  truncateBubbleText,
} = api;


assert.equal(api.joinUrl('/api/', 'state-machine-runs'), '/api/state-machine-runs');
assert.deepEqual(api.unwrapEnvelope({ code: 20000, request_id: 'req', data: { ok: true } }), { ok: true });
assert.deepEqual(api.unwrapEnvelope({ data: { ok: true } }), { data: { ok: true } });
const rawResponse = await requestJson('/api', '/raw', {}, async () => new Response(JSON.stringify({ ok: true }), { status: 200 }));
assert.deepEqual(rawResponse, { ok: true });
await assert.rejects(
  requestJson('/api', '/conflict', {}, async () => new Response(JSON.stringify({ message: 'expired' }), { status: 409 })),
  (error) => error instanceof ApiRequestError && error.status === 409 && error.conflict && error.message === 'expired',
);
const abortError = new Error('aborted');
abortError.name = 'AbortError';
await assert.rejects(requestJson('/api', '/abort', {}, async () => { throw abortError; }), (error) => error.name === 'AbortError');

const params = normalizePanelParams({
  runId: 'run-example',
  groupId: 'group-example',
  sessionId: 'session-example',
  phase: 'voting',
  round: 2,
  host: { actorId: 'host', displayName: 'Host' },
  seatOrder: ['a', 'b', 'c'],
  players: [
    { actorId: 'a', displayName: 'A', isHuman: true },
    { actorId: 'b', displayName: 'B' },
    { actorId: 'c', displayName: 'C', eliminated: true },
  ],
  nodeActorMap: { 'node-a': 'a', 'node-b': 'b', 'node-host': 'host' },
  currentViewerActorId: 'a',
  voteCandidates: [
    { actorId: 'b', displayName: 'B', eligible: true },
    { actorId: 'c', displayName: 'C', eligible: true, eliminated: true },
    { actorId: 'a', displayName: 'A', eligible: false },
  ],
});

assert.equal(getSeatCoordinates(params.seatOrder).map((seat) => seat.actorId).join(','), 'a,b,c');
assert.deepEqual(eligibleVoteCandidates(params.voteCandidates), [{ actorId: 'b', displayName: 'B', eligible: true }]);
assert.equal(serializeVoteContent('b'), '{"kind":"vote","target_actor_id":"b"}');
assert.deepEqual(truncateBubbleText('你好世界', 3), { text: '你好…', truncated: true });

const messages = [
  { id: 'old', sequence: 1, content: 'old', metadata: { state_machine: { event: 'output', run_id: 'run-example', node_id: 'node-a', attempt: 1 } } },
  { id: 'new', sequence: 2, content: { text: 'new' }, metadata: { state_machine: { event: 'output', run_id: 'run-example', node_id: 'node-a', attempt: 1 } } },
  { id: 'host', sequence: 3, content: 'announcement', metadata: { state_machine: { event: 'output', run_id: 'run-example', node_id: 'node-host', attempt: 1 } } },
  { id: 'other-run', sequence: 99, content: 'private', metadata: { state_machine: { event: 'output', run_id: 'other-run', node_id: 'node-a', attempt: 1 } } },
];
const events = mapPublicOutputEvents(messages, params);
assert.equal(events.length, 2);
const model = normalizeUndercoverGameViewModel(params, {
  run: { run_id: 'run-example', status: 'running', updated_at: 4 },
  nodes: [
    { node_id: 'node-a', status: 'completed', attempt: 1 },
    { node_id: 'node-b', status: 'running', attempt: 1 },
  ],
}, [{ node_id: 'node-a', instruction: 'respond' }], messages);
assert.equal(model.actors.find((actor) => actor.actor.actorId === 'a').latestOutput.text, 'new');
assert.equal(model.actors.find((actor) => actor.actor.actorId === 'host').latestOutput.text, 'announcement');
assert.equal(model.actors.find((actor) => actor.actor.actorId === 'c').state, 'eliminated');
assert.equal(model.terminal, false);

assert.throws(() => normalizePanelParams({ phase: 'speaking', round: 1 }), /runId is required/);
assert.throws(() => normalizePanelParams({
  runId: 'r', groupId: 'g', sessionId: 's', phase: 'speaking', round: 1,
  host: { actorId: 'h', displayName: 'H' }, seatOrder: ['a', 'a'], players: [{ actorId: 'a', displayName: 'A' }], nodeActorMap: {},
}), /duplicate actor IDs/);
console.log('Focused undercover-game behavior tests passed.');
