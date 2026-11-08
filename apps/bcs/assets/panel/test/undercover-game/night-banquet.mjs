import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { JSDOM } from 'jsdom';

const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://local.test/' });
Object.assign(globalThis, {
  window: dom.window,
  document: dom.window.document,
  HTMLElement: dom.window.HTMLElement,
  Node: dom.window.Node,
  getComputedStyle: dom.window.getComputedStyle,
  IS_REACT_ACT_ENVIRONMENT: true,
});
window.confirm = () => true;
let resizeCallback;
globalThis.ResizeObserver = class {
  constructor(callback) { resizeCallback = callback; }
  observe() {}
  disconnect() {}
};
window.ResizeObserver = globalThis.ResizeObserver;

const ReactModule = await import('react');
const React = ReactModule.default;
const { act } = ReactModule;
const { createRoot } = await import('react-dom/client');
const require = createRequire(import.meta.url);
const api = require('../../dist/index.umd.js');
const { UndercoverGamePanel, countdownText, formatCountdown, nightDialogue, nightPhaseLabel, nightSeatSlots, nightSteps } = api;

const response = (body, status = 200) => new Response(JSON.stringify({ code: 20000, data: body }), { status, headers: { 'content-type': 'application/json' } });
const settle = async (ms = 0) => { await Promise.resolve(); await Promise.resolve(); if (ms) await new Promise((resolve) => setTimeout(resolve, ms)); await Promise.resolve(); };
const text = (panel) => panel.container.textContent ?? '';
const button = (panel, label) => [...panel.container.querySelectorAll('button')].find((node) => node.textContent?.includes(label));
const setInputValue = (element, value) => { const setter = Object.getOwnPropertyDescriptor(element.constructor.prototype, 'value').set; setter.call(element, value); element.dispatchEvent(new window.Event('input', { bubbles: true })); };

// ---- Pure night-view helpers: deterministic, data-driven, no fake time ----
assert.deepEqual(nightSteps('speaking', 2, 6, false).map((step) => step.state), ['done', 'active', 'pending', 'pending']);
assert.deepEqual(nightSteps('voting', 0, 6, false).map((step) => step.state), ['done', 'done', 'done', 'active']);
assert.deepEqual(nightSteps('complete', 6, 6, true).map((step) => step.state), ['done', 'done', 'done', 'done']);
assert.deepEqual(nightSteps('pk_voting', 0, 6, false).map((step) => step.state), ['done', 'done', 'done', 'active']);
assert.equal(nightPhaseLabel('speaking'), '发言');
assert.equal(nightPhaseLabel('pk_voting'), 'PK 投票');
assert.equal(nightPhaseLabel('reveal'), '终局');
assert.deepEqual(nightSeatSlots(6).map((slot) => [slot.x, slot.y]), [[20, 69], [35, 53], [64, 53], [79, 70], [62, 81], [36, 81]]);
assert.deepEqual(nightSeatSlots(6), nightSeatSlots(6));
assert.equal(nightSeatSlots(4).length, 4);
assert.equal(nightSeatSlots(0).length, 0);
for (const slot of nightSeatSlots(8)) assert.ok(slot.x > 0 && slot.x < 100 && slot.y > 0 && slot.y < 100);
assert.equal(formatCountdown(125000), '02:05');
assert.equal(formatCountdown(999), '00:01');
assert.equal(formatCountdown(0), '00:00');
assert.equal(countdownText(undefined, 0), undefined);
assert.equal(countdownText(Date.now() + 61000, Date.now()), '01:01');
const sampleModel = {
  params: { publicHistory: [{ round: 1, stage: 'regular', speeches: [{ actorId: 'a', seatNumber: 1, displayName: '甲', text: '公开历史' }] }] },
  actors: [
    { actor: { actorId: 'a', displayName: '甲', seatNumber: 1 }, kind: 'player', latestOutput: { text: '节点实时发言', pending: false, sequence: 2, timestamp: 1 }, publicHistory: [] },
    { actor: { actorId: 'h', displayName: '主持' }, kind: 'host', latestOutput: { text: '主持公开播报', pending: false, sequence: 3, timestamp: 2 }, publicHistory: [] },
  ],
  pendingHumanNode: undefined,
  terminal: false,
};
assert.equal(nightDialogue(sampleModel, { voting: false, showHostOutput: true })?.text, '节点实时发言');
assert.equal(nightDialogue(sampleModel, { voting: true, showHostOutput: true })?.text, '主持公开播报');
assert.equal(nightDialogue(sampleModel, { voting: true, showHostOutput: false }), undefined);
assert.equal(nightDialogue({ ...sampleModel, actors: [sampleModel.actors[0]] }, { voting: false, showHostOutput: true })?.text, '节点实时发言');
assert.equal(nightDialogue({ ...sampleModel, actors: [{ ...sampleModel.actors[0], latestOutput: undefined }, sampleModel.actors[1]] }, { voting: false, showHostOutput: true })?.text, '公开历史');
assert.equal(nightDialogue({ ...sampleModel, actors: [] }, { voting: false, showHostOutput: true }), undefined);

// ---- Production sources keep the privacy/injection guardrails ----
const sourceDir = new URL('../../src/undercover-game/', import.meta.url);
for (const file of ['UndercoverGamePanel.tsx', 'RoomScene.tsx', 'NightDialogue.tsx', 'nightView.ts', 'nightStage.ts', 'countdown.ts', 'useCountdown.ts', 'assets.ts', 'GameOverDialog.tsx']) {
  const source = readFileSync(new URL(file, sourceDir), 'utf8');
  assert.doesNotMatch(source, /location\.href/, `${file} must not navigate away`);
  assert.doesNotMatch(source, /\bROSTER\b/, `${file} must not hardcode the static demo roster`);
  assert.doesNotMatch(source, /dangerouslySetInnerHTML|\.innerHTML\b/, `${file} must render dynamic text through React`);
}

const players = [
  { actorId: 'p-c', displayName: '夜宴宾客·丙', seatNumber: 3 },
  { actorId: 'p-a', displayName: '夜宴宾客·甲', seatNumber: 1, isHuman: true },
  { actorId: 'p-e', displayName: '夜宴宾客·戊', seatNumber: 5 },
  { actorId: 'p-b', displayName: '夜宴宾客·乙', seatNumber: 2 },
  { actorId: 'p-f', displayName: '夜宴宾客·己', seatNumber: 6 },
  { actorId: 'p-d', displayName: '夜宴宾客·丁', seatNumber: 4, eliminated: true, alive: false },
];
const seatOrder = ['p-c', 'p-a', 'p-e', 'p-b', 'p-f', 'p-d'];
const base = {
  runId: 'run-night', groupId: 'group-night', sessionId: 'session-night', gameSessionId: 'game-night',
  phase: 'speaking', round: 2, attempt: 1, host: { actorId: 'host-night', displayName: '夜宴主持人' },
  seatOrder, turnOrder: ['p-a', 'p-b', 'p-c', 'p-e', 'p-f'], players,
  nodeActorMap: { 'node-a': 'p-a', 'node-b': 'p-b', 'node-c': 'p-c', 'node-d': 'p-d', 'node-e': 'p-e', 'node-f': 'p-f', 'node-host': 'host-night' },
  currentViewerActorId: 'p-a', rules: { speechMaxChars: 12, forbidOwnWord: true },
  autoRefresh: false, pollingInterval: 5,
};
function mount(props, width = 760) {
  const container = document.createElement('div');
  document.body.appendChild(container);
  Object.defineProperty(container, 'getBoundingClientRect', { value: () => ({ width, height: 700, top: 0, left: 0, right: width, bottom: 700, x: 0, y: 0, toJSON() {} }) });
  const root = createRoot(container);
  root.render(React.createElement(UndercoverGamePanel, props));
  return { container, render: (next) => root.render(React.createElement(UndercoverGamePanel, next)), unmount: () => { root.unmount(); container.remove(); } };
}
function fetchFor({ graph, pending = [], details = {}, posts = [], respondStatus = 200, requests = [] }) {
  return async (url, init = {}) => {
    const path = new URL(url, 'http://local.test').pathname;
    requests.push(path);
    if (init.method === 'POST' && path.endsWith('/respond')) {
      posts.push(JSON.parse(init.body));
      if (respondStatus !== 200) return response({ message: 'expired' }, respondStatus);
      return response({ accepted: true });
    }
    if (path.endsWith('/graph')) return response(graph);
    if (path.endsWith('/pending-human-nodes')) return response(pending);
    if (path.includes('/nodes/')) {
      const nodeId = path.split('/').at(-1);
      if (details[nodeId] === undefined) return response({ node: { node_id: nodeId, status: 'completed', attempt: 1 } });
      return response(details[nodeId]);
    }
    return response({}, 404);
  };
}

const originalFetch = globalThis.fetch;
try {
  // ---- Seats, names, HUD steps, and timer all come from real params/graph/pending ----
  const requests = [];
  globalThis.fetch = fetchFor({
    graph: { run: { run_id: base.runId, status: 'running' }, nodes: [{ node_id: 'node-b', status: 'completed', attempt: 1 }] },
    pending: [{ node_id: 'node-a', attempt: 1, instruction: '[UNDERCOVER_UI_CONTEXT_V1]' + JSON.stringify({ action: 'speech', round: 2, seatNumber: 1, word: '夜莺', maxChars: 12, forbidOwnWord: true }) + '[/UNDERCOVER_UI_CONTEXT_V1]', timeout_deadline_ms: Date.now() + 125000 }],
    requests,
  });
  let panel;
  await act(async () => { panel = mount(base); await settle(10); });
  const seatIds = [...panel.container.querySelectorAll('[data-actor-id]')].map((node) => node.getAttribute('data-actor-id'));
  assert.deepEqual(seatIds, seatOrder, 'seat ring follows params.seatOrder, not a hardcoded roster');
  assert.ok(panel.container.querySelector('[data-region="workflow-board"]'), 'normal rounds keep the workflow board even when historical players are eliminated');
  assert.match(panel.container.querySelector('[data-actor-id="p-a"] [data-seat-plate]').textContent, /1号 · 夜宴宾客·甲（你）/);
  assert.match(panel.container.querySelector('[data-actor-id="p-d"] [data-seat-plate]').textContent, /4号 · 夜宴宾客·丁/);
  const sceneText = text(panel);
  for (const stale of ['稳健老陈', '戏精阿波', '话痨小满', '逻辑控林工', '和事佬阿和', '水煮肉片', '房间 728059']) assert.ok(!sceneText.includes(stale), `dynamic view must not resurrect demo data: ${stale}`);
  assert.deepEqual([...panel.container.querySelectorAll('[data-step]')].map((node) => [node.getAttribute('data-step'), node.getAttribute('data-step-state')]), [['drawing', 'done'], ['describe', 'active'], ['discuss', 'pending'], ['vote', 'pending']]);
  const timer = panel.container.querySelector('[role="timer"]');
  assert.ok(timer, 'a real deadline from the pending HumanInput renders a timer');
  assert.match(timer.textContent, /^剩余时间02:0[0-5]$/);
  assert.ok(!sceneText.includes('84') && !sceneText.includes('01:24'), 'no static demo countdown');
  await act(async () => panel.unmount());

  // No server deadline means no invented countdown at all.
  globalThis.fetch = fetchFor({ graph: { run: { run_id: base.runId, status: 'running' }, nodes: [] }, pending: [] });
  await act(async () => { panel = mount(base); await settle(10); });
  assert.equal(panel.container.querySelector('[role="timer"]'), null);
  assert.match(text(panel), /从容思考，留意场上发言/);
  await act(async () => panel.unmount());

  // Voting phase advances the public step row from the same real params.
  globalThis.fetch = fetchFor({ graph: { run: { run_id: base.runId, status: 'running' }, nodes: [] }, pending: [] });
  await act(async () => { panel = mount({ ...base, phase: 'voting', voteCandidates: [{ actorId: 'p-b', displayName: '夜宴宾客·乙', seatNumber: 2, eligible: true }] }); await settle(10); });
  assert.deepEqual([...panel.container.querySelectorAll('[data-step]')].map((node) => node.getAttribute('data-step-state')), ['done', 'done', 'done', 'active']);
  assert.match(text(panel), /投票阶段/);
  await act(async () => panel.unmount());

  // ---- Latest public speech is read from the existing node-detail endpoint ----
  const speechDetail = { node: { node_id: 'node-b', run_id: base.runId, status: 'completed', attempt: 1, completed_at: 2, artifact_text: '节点详情里的真实公开发言' } };
  const detailRequests = [];
  globalThis.fetch = fetchFor({
    graph: { run: { run_id: base.runId, status: 'running' }, nodes: [{ node_id: 'node-b', status: 'completed', attempt: 1 }] },
    details: { 'node-b': speechDetail },
    requests: detailRequests,
  });
  await act(async () => { panel = mount(base); await settle(10); });
  const dialogue = panel.container.querySelector('[data-region="dialogue"]');
  assert.ok(dialogue);
  assert.match(dialogue.textContent, /2号/);
  assert.match(dialogue.textContent, /夜宴宾客·乙/);
  assert.match(dialogue.textContent, /节点详情里的真实公开发言/);
  assert.ok(detailRequests.some((path) => path.endsWith('/nodes/node-b')));
  assert.equal(detailRequests.some((path) => path.includes('/sessions/')), false);
  await act(async () => panel.unmount());

  // ---- Private word comes only from this viewer's HumanInput context ----
  const wordInstruction = '[UNDERCOVER_UI_CONTEXT_V1]' + JSON.stringify({ action: 'speech', round: 2, seatNumber: 1, word: '夜莺', maxChars: 12, forbidOwnWord: true }) + '[/UNDERCOVER_UI_CONTEXT_V1]';
  assert.doesNotMatch(JSON.stringify(base), /夜莺/, 'public panel params never carry the private word');
  globalThis.fetch = fetchFor({
    graph: { run: { run_id: base.runId, status: 'running' }, nodes: [{ node_id: 'node-a', status: 'ready', attempt: 1 }] },
    pending: [{ node_id: 'node-a', attempt: 1, instruction: wordInstruction }],
  });
  await act(async () => { panel = mount(base); await settle(10); });
  assert.ok(panel.container.querySelector('[data-region="word-card"]'));
  assert.match(text(panel), /••••/);
  assert.ok(!text(panel).includes('夜莺'), 'word stays hidden until the viewer asks for it');
  await act(async () => { button(panel, '显示词语').click(); await settle(); });
  assert.match(text(panel), /夜莺/);
  assert.equal(panel.container.querySelector('input[value="__abstain__"]'), null, 'speech action has no vote inputs');
  await act(async () => panel.unmount());

  // ---- A new pending action identity clears a previously revealed word ----
  // Deterministic sequence: initial load shows the current word; one explicit
  // refresh swaps in a new pending identity; no background polling is involved.
  const wordA = '[UNDERCOVER_UI_CONTEXT_V1]' + JSON.stringify({ action: 'speech', round: 2, seatNumber: 1, word: '夜莺', maxChars: 12, forbidOwnWord: true }) + '[/UNDERCOVER_UI_CONTEXT_V1]';
  const wordB = '[UNDERCOVER_UI_CONTEXT_V1]' + JSON.stringify({ action: 'speech', round: 2, seatNumber: 2, word: '月桂', maxChars: 12, forbidOwnWord: true }) + '[/UNDERCOVER_UI_CONTEXT_V1]';
  let pendingLoads = 0;
  globalThis.fetch = async (url) => {
    const path = new URL(url, 'http://local.test').pathname;
    if (path.endsWith('/graph')) return response({ run: { run_id: base.runId, status: 'running' }, nodes: [{ node_id: 'node-a', status: 'ready' }] });
    if (path.endsWith('/pending-human-nodes')) {
      pendingLoads += 1;
      return response([pendingLoads === 1 ? { node_id: 'node-a', attempt: 1, instruction: wordA } : { node_id: 'node-a2', attempt: 1, instruction: wordB }]);
    }
    return response({}, 404);
  };
  await act(async () => { panel = mount({ ...base, nodeActorMap: { ...base.nodeActorMap, 'node-a2': 'p-a' }, autoRefresh: false }); await settle(); });
  assert.match(text(panel), /••••/);
  assert.ok(!text(panel).includes('夜莺'), 'the current word is hidden on mount');
  await act(async () => { button(panel, '显示词语').click(); await settle(); });
  assert.match(text(panel), /夜莺/, 'the current pending action can reveal its own word');
  assert.equal(pendingLoads, 1);
  await act(async () => { button(panel, '刷新').click(); await settle(); await settle(); });
  assert.ok(pendingLoads >= 2, 'the explicit refresh fetched the new pending identity');
  assert.ok(!text(panel).includes('夜莺'), 'a new pending action hides the previous private word again');
  assert.match(text(panel), /••••/);
  assert.ok(!text(panel).includes('月桂'), 'the next word also starts hidden');
  await act(async () => panel.unmount());

  // ---- Host node-detail artifacts never expose a structured ballot during voting ----
  globalThis.fetch = fetchFor({
    graph: { run: { run_id: base.runId, status: 'running' }, nodes: [{ node_id: 'node-host', status: 'completed', attempt: 1 }] },
    details: { 'node-host': { node: { node_id: 'node-host', run_id: base.runId, status: 'completed', attempt: 1, artifact_text: '{"kind":"vote","target_actor_id":"p-c"}' } } },
    pending: [],
  });
  await act(async () => { panel = mount({ ...base, phase: 'voting' }); await settle(10); });
  assert.ok(!text(panel).includes('target_actor_id'));
  assert.ok(!text(panel).includes('"kind":"vote"'));
  assert.equal(panel.container.querySelector('[data-region="host-broadcast"]'), null);
  await act(async () => panel.unmount());

  // ---- PK view keeps candidates, hides abstain, and reuses the same submit path ----
  const pkParams = {
    ...base,
    phase: 'pk_voting',
    pkCandidates: ['p-a', 'p-b'],
    voteCandidates: [
      { actorId: 'p-a', displayName: '夜宴宾客·甲', seatNumber: 1, eligible: true },
      { actorId: 'p-b', displayName: '夜宴宾客·乙', seatNumber: 2, eligible: true },
    ],
  };
  const pkInstruction = '[UNDERCOVER_UI_CONTEXT_V1]' + JSON.stringify({ action: 'vote', round: 2, seatNumber: 1, allowAbstain: false }) + '[/UNDERCOVER_UI_CONTEXT_V1]';
  const pkPosts = [];
  globalThis.fetch = fetchFor({
    graph: { run: { run_id: base.runId, status: 'running' }, nodes: [{ node_id: 'node-a', status: 'ready', attempt: 1 }] },
    pending: [{ node_id: 'node-a', attempt: 1, instruction: pkInstruction }],
    posts: pkPosts,
  });
  await act(async () => { panel = mount(pkParams); await settle(10); });
  assert.match(text(panel), /PK 投票/);
  assert.equal(panel.container.querySelectorAll('[data-pk-candidate="true"]').length, 2);
  assert.equal(panel.container.querySelector('input[value="__abstain__"]'), null, 'PK never offers abstention');
  await act(async () => {
    panel.container.querySelector('input[value="p-b"]').click();
    panel.container.querySelector('input[type="checkbox"]').click();
    await settle();
    panel.container.querySelector('form').dispatchEvent(new window.Event('submit', { bubbles: true, cancelable: true }));
    await settle(5);
  });
  assert.deepEqual(pkPosts, [{ content: '{"kind":"vote","target_actor_id":"p-b"}' }]);
  await act(async () => panel.unmount());

  // A hidden host broadcast and a hidden timer follow the existing display flags.
  globalThis.fetch = fetchFor({
    graph: { run: { run_id: base.runId, status: 'running' }, nodes: [{ node_id: 'node-host', status: 'completed', attempt: 1 }] },
    details: { 'node-host': { node: { node_id: 'node-host', run_id: base.runId, status: 'completed', attempt: 1, artifact_text: '主持人公开播报' } } },
    pending: [{ node_id: 'node-a', attempt: 1, instruction: wordInstruction, timeout_deadline_ms: Date.now() + 61000 }],
  });
  await act(async () => { panel = mount({ ...base, display: { showHostOutput: false, showTimer: false } }); await settle(10); });
  assert.equal(panel.container.querySelector('[role="timer"]'), null);
  assert.equal(panel.container.querySelector('[data-region="host-broadcast"]'), null);
  assert.ok(!text(panel).includes('主持人公开播报'));
  await act(async () => panel.unmount());

  // ---- Speech submission keeps validation and serialization contracts ----
  const speechPosts = [];
  globalThis.fetch = fetchFor({
    graph: { run: { run_id: base.runId, status: 'running' }, nodes: [{ node_id: 'node-a', status: 'ready', attempt: 1 }] },
    pending: [{ node_id: 'node-a', attempt: 1, instruction: wordInstruction }],
    posts: speechPosts,
  });
  await act(async () => { panel = mount(base); await settle(10); });
  const textarea = panel.container.querySelector('textarea');
  await act(async () => { setInputValue(textarea, '夜莺很好听'); await settle(); panel.container.querySelector('form').dispatchEvent(new window.Event('submit', { bubbles: true, cancelable: true })); await settle(); });
  assert.match(text(panel), /不能包含你自己的词/);
  assert.equal(speechPosts.length, 0);
  await act(async () => { setInputValue(panel.container.querySelector('textarea'), ' 月色很美 '); await settle(); panel.container.querySelector('form').dispatchEvent(new window.Event('submit', { bubbles: true, cancelable: true })); await settle(5); });
  assert.deepEqual(speechPosts, [{ content: '月色很美' }]);
  await act(async () => panel.unmount());

  // ---- Vote requires explicit confirmation, serializes canonical content, hides targets ----
  const voteInstruction = '[UNDERCOVER_UI_CONTEXT_V1]' + JSON.stringify({ action: 'vote', round: 2, seatNumber: 1, word: '夜莺', allowAbstain: true }) + '[/UNDERCOVER_UI_CONTEXT_V1]';
  const voteParams = {
    ...base,
    phase: 'voting',
    voteCandidates: [
      { actorId: 'p-b', displayName: '夜宴宾客·乙', seatNumber: 2, eligible: true },
      { actorId: 'p-d', displayName: '夜宴宾客·丁', seatNumber: 4, eligible: true, eliminated: true },
    ],
  };
  const votePosts = [];
  globalThis.fetch = fetchFor({
    graph: { run: { run_id: base.runId, status: 'running' }, nodes: [{ node_id: 'node-a', status: 'ready', attempt: 1 }, { node_id: 'node-b', status: 'completed', attempt: 1 }] },
    pending: [{ node_id: 'node-a', attempt: 1, instruction: voteInstruction }],
    details: { 'node-b': { node: { node_id: 'node-b', run_id: base.runId, status: 'completed', attempt: 1, artifact_text: '{"kind":"vote","target_actor_id":"p-a"}' } } },
    posts: votePosts,
  });
  await act(async () => { panel = mount(voteParams); await settle(10); });
  assert.ok(panel.container.querySelector('input[value="__abstain__"]'), 'allowAbstain from private context keeps the explicit abstain card');
  assert.ok(!text(panel).includes('target_actor_id'), 'raw vote payloads are never rendered');
  const voteOption = panel.container.querySelector('input[value="p-b"]');
  const confirmation = panel.container.querySelector('input[type="checkbox"]');
  assert.equal(button(panel, '确认并投票').disabled, true);
  await act(async () => { voteOption.click(); await settle(); });
  assert.equal(button(panel, '确认并投票').disabled, true, 'vote needs the second confirmation');
  await act(async () => { confirmation.click(); await settle(); });
  assert.equal(button(panel, '确认并投票').disabled, false);
  await act(async () => { panel.container.querySelector('form').dispatchEvent(new window.Event('submit', { bubbles: true, cancelable: true })); await settle(5); });
  assert.deepEqual(votePosts, [{ content: '{"kind":"vote","target_actor_id":"p-b"}' }]);
  await act(async () => panel.unmount());

  // Explicit abstain keeps its canonical serialization.
  const abstainPosts = [];
  globalThis.fetch = fetchFor({
    graph: { run: { run_id: base.runId, status: 'running' }, nodes: [{ node_id: 'node-a', status: 'ready', attempt: 1 }] },
    pending: [{ node_id: 'node-a', attempt: 1, instruction: voteInstruction }],
    posts: abstainPosts,
  });
  await act(async () => { panel = mount(voteParams); await settle(10); });
  await act(async () => {
    panel.container.querySelector('input[value="__abstain__"]').click();
    panel.container.querySelector('input[type="checkbox"]').click();
    await settle();
    panel.container.querySelector('form').dispatchEvent(new window.Event('submit', { bubbles: true, cancelable: true }));
    await settle(5);
  });
  assert.deepEqual(abstainPosts, [{ content: '{"kind":"vote","abstain":true}' }]);
  await act(async () => panel.unmount());

  // ---- Stale/conflict submissions surface the recoverable error and refresh ----
  const conflictRequests = [];
  globalThis.fetch = fetchFor({
    graph: { run: { run_id: base.runId, status: 'running' }, nodes: [{ node_id: 'node-a', status: 'ready', attempt: 1 }] },
    pending: [{ node_id: 'node-a', attempt: 1, instruction: voteInstruction }],
    respondStatus: 409,
    requests: conflictRequests,
  });
  await act(async () => { panel = mount(voteParams); await settle(10); });
  const graphsBefore = conflictRequests.filter((path) => path.endsWith('/graph')).length;
  await act(async () => {
    panel.container.querySelector('input[value="p-b"]').click();
    panel.container.querySelector('input[type="checkbox"]').click();
    await settle();
    panel.container.querySelector('form').dispatchEvent(new window.Event('submit', { bubbles: true, cancelable: true }));
    await settle(10);
  });
  assert.match(text(panel), /过期或被处理/);
  assert.ok(conflictRequests.filter((path) => path.endsWith('/graph')).length > graphsBefore, 'conflict triggers a fresh refresh');
  await act(async () => panel.unmount());

  // ---- Dynamic text is React-escaped; markup in names/speech stays text ----
  const xssName = '夜宴<img src=x onerror="window.__nightXss=1">宾客';
  const xssParams = { ...base, seatOrder: ['p-a', 'p-b'], turnOrder: ['p-a', 'p-b'], players: [{ actorId: 'p-a', displayName: xssName, seatNumber: 1, isHuman: true }, { actorId: 'p-b', displayName: '夜宴宾客·乙', seatNumber: 2 }] };
  globalThis.fetch = fetchFor({
    graph: { run: { run_id: base.runId, status: 'running' }, nodes: [{ node_id: 'node-b', status: 'completed', attempt: 1 }] },
    details: { 'node-b': { node: { node_id: 'node-b', run_id: base.runId, status: 'completed', attempt: 1, artifact_text: '<script>window.__nightXss=2</script>公开发言' } } },
  });
  await act(async () => { panel = mount(xssParams); await settle(10); });
  assert.equal(window.__nightXss, undefined);
  assert.equal(panel.container.querySelector('img'), null);
  assert.equal(panel.container.querySelector('script'), null);
  assert.ok(text(panel).includes(xssName), 'markup is preserved as literal text');
  assert.ok(text(panel).includes('<script>window.__nightXss=2</script>公开发言'));
  await act(async () => panel.unmount());
} finally {
  globalThis.fetch = originalFetch;
}
console.log('Night-banquet view tests passed: real seats/steps/timer, node-detail speech, private-word reveal, speech/vote/abstain submissions, conflict recovery, and React escaping.');
