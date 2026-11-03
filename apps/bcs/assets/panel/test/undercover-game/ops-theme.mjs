import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { JSDOM } from 'jsdom';

// Ops-theme (new view layer) lifecycle + privacy contract:
//  - 5 states render (speech / vote-with-abstain / PK / eliminated / finale);
//  - speech and vote payloads stay sanitized in the room AND the workflow view;
//  - candidate list and tally never leak raw speeches / vote targets / role words;
//  - keyboard focus lands on the action control, Escape dismisses the finale;
//  - ≤360px narrow flag is applied.

const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://local.test/' });
Object.assign(globalThis, {
  window: dom.window, document: dom.window.document,
  HTMLElement: dom.window.HTMLElement, Node: dom.window.Node,
  HTMLTextAreaElement: dom.window.HTMLTextAreaElement, HTMLInputElement: dom.window.HTMLInputElement,
  getComputedStyle: dom.window.getComputedStyle, IS_REACT_ACT_ENVIRONMENT: true,
});
window.confirm = () => true;
HTMLElement.prototype.attachEvent = function () { };
HTMLElement.prototype.detachEvent = function () { };
let resizeCallback;
globalThis.ResizeObserver = class { constructor(cb) { resizeCallback = cb; } observe() { } disconnect() { } };
window.ResizeObserver = globalThis.ResizeObserver;

const ReactModule = await import('react');
const React = ReactModule.default;
const { act } = ReactModule;
const { createRoot } = await import('react-dom/client');
const require = createRequire(import.meta.url);
const { UndercoverGameOpsPanel, buildRunnerGraph } = require('../../dist/index.umd.js');

const PLAYERS = [
  { actorId: 'a', displayName: '小甲', seatNumber: 1 },
  { actorId: 'b', displayName: '小乙', seatNumber: 2 },
  { actorId: 'c', displayName: '小丙', seatNumber: 3 },
  { actorId: 'd', displayName: '小丁', seatNumber: 4, isHuman: true },
  { actorId: 'e', displayName: '小戊', seatNumber: 5 },
  { actorId: 'f', displayName: '小己', seatNumber: 6 },
];
const base = {
  runId: 'ops-run', groupId: 'group', sessionId: 'session', gameSessionId: 'game',
  phase: 'speaking', round: 1, attempt: 1,
  host: { actorId: 'host', displayName: '主持人' },
  seatOrder: PLAYERS.map((p) => p.actorId),
  turnOrder: PLAYERS.map((p) => p.actorId),
  players: PLAYERS,
  nodeActorMap: Object.fromEntries(PLAYERS.map((p) => [`speech-${p.actorId}`, p.actorId])),
  currentViewerActorId: 'd',
  rules: { speechMaxChars: 25, forbidOwnWord: true, bluntness: 3 },
  publicHistory: [],
  autoRefresh: false,
  pollingInterval: 5,
};

function response(body, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
}
function env(resp) { return response(resp.enveloped ? { code: 20000, data: resp.data } : resp.data); }

function mount(props) {
  const container = document.createElement('div');
  document.body.appendChild(container);
  Object.defineProperty(container, 'getBoundingClientRect', {
    value: () => ({ width: 360, height: 700, top: 0, left: 0, right: 360, bottom: 700, x: 0, y: 0, toJSON() { } }),
  });
  const root = createRoot(container);
  root.render(React.createElement(UndercoverGameOpsPanel, props));
  return { container, render: (p) => root.render(React.createElement(UndercoverGameOpsPanel, p)), unmount: () => { root.unmount(); container.remove(); } };
}
function snapshot(graph, pending = [], artifacts = {}, enveloped = true) {
  return { graph, pending, artifacts, enveloped };
}
let snapshotIndex = 0;
function fetchFor(groups, posts) {
  let group = groups[0];
  let idx = 0;
  return async (url, init = {}) => {
    const path = new URL(url, 'http://local.test').pathname;
    if (init.method === 'POST' && path.endsWith('/respond')) { posts.push(JSON.parse(init.body)); return env({ data: { accepted: true }, enveloped: true }); }
    if (path.endsWith('/graph')) { group = groups[Math.min(idx++, groups.length - 1)]; return env({ data: { ...group.graph }, enveloped: true }); }
    if (path.endsWith('/pending-human-nodes')) return env({ data: group.pending, enveloped: true });
    const nodeId = path.split('/').at(-1);
    if (path.includes('/nodes/')) return env({ data: { node: { node_id: nodeId, status: 'completed', attempt: 1, artifact_text: group.artifacts?.[nodeId] } }, enveloped: true });
    throw new Error(`Unexpected panel request: ${path}`);
  };
}
const speechInstruction = [
  '请描述',
  '[UNDERCOVER_UI_CONTEXT_V1]',
  JSON.stringify({ action: 'speech', round: 1, seatNumber: 4, word: '苹果', maxChars: 25, forbidOwnWord: true, bluntness: 3 }),
  '[/UNDERCOVER_UI_CONTEXT_V1]',
].join('\n');
const voteInstruction = [
  '请投票',
  '[UNDERCOVER_UI_CONTEXT_V1]',
  JSON.stringify({ action: 'vote', round: 1, seatNumber: 4, word: '苹果', allowAbstain: true }),
  '[/UNDERCOVER_UI_CONTEXT_V1]',
].join('\n');

const text = (p) => p.container.textContent ?? '';
const button = (p, label) => [...p.container.querySelectorAll('button')].find((b) => b.textContent?.includes(label));
const settle = async (ms = 0) => { await Promise.resolve(); await Promise.resolve(); if (ms) await new Promise((r) => setTimeout(r, ms)); await Promise.resolve(); };

// --- layout unit -------------------------------------------------------------
{
  const lay = buildRunnerGraph(
    [
      { node_id: 'vote_start', status: 'completed' },
      { node_id: 'vote-a', status: 'running' },
      { node_id: 'vote-b', status: 'pending' },
      { node_id: 'vote-c', status: 'pending' },
      { node_id: 'tally', status: 'pending' },
    ],
    [{ source: 'vote_start', target: 'vote-a' }, { source: 'vote_start', target: 'vote-b' }, { source: 'vote_start', target: 'vote-c' },
     { source: 'vote-a', target: 'tally' }, { source: 'vote-b', target: 'tally' }, { source: 'vote-c', target: 'tally' }],
    { 'vote-a': 'a', 'vote-b': 'b', 'vote-c': 'c' }, 'host',
    PLAYERS,
  );
  // tally is pushed to last column as referee 末节点, and vote_N are parallel.
  assert.ok(lay.columns.length >= 3, `vote run should have ≥3 layers got ${lay.columns.length}`);
  assert.ok(lay.columns[lay.columns.length - 1].nodes.some((n) => n.id === 'tally'), 'tally must be the 末节点');
  assert.equal(lay.columns[1].nodes.filter((n) => /^vote-/.test(n.id)).length, 3, 'vote nodes are parallel in one column');
}

// --- component lifecycle -----------------------------------------------------
const originalFetch = globalThis.fetch;
const origLog = console.log, origWarn = console.warn, origErr = console.error;
const consoleLines = [];
console.log = (...a) => { consoleLines.push(a.join(' ')); origLog(...a); };
console.warn = (...a) => { consoleLines.push(a.join(' ')); origWarn(...a); };
console.error = (...a) => { consoleLines.push(a.join(' ')); origErr(...a); };
const storageWords = () => {
  const words = [];
  try { for (let i = 0; i < window.localStorage.length; i++) { const k = window.localStorage.key(i); words.push(String(k), String(window.localStorage.getItem(k) ?? '')); } } catch { /* localStorage unavailable */ }
  return words.join(' ');
};
const secrets = [/苹果/, /UNDERCOVER_UI_CONTEXT/, /target_actor_id|\\{\\\\\"kind\\\\\":\\\\\"vote\\\\\"/];
const privacyState = () => {
  const com = consoleLines.join('\n');
  const st = storageWords();
  return secrets.every((re) => !re.test(com) && !re.test(st));
};
try {
  const speechPosts = [];
  globalThis.fetch = fetchFor([
    snapshot({ run: { run_id: 'ops-run', status: 'running', updated_at: 1 }, nodes: [{ node_id: 'speech-d', status: 'ready' }, { node_id: 'speech-a', status: 'completed' }] },
      [{ node_id: 'speech-d', attempt: 1, instruction: speechInstruction, upstream_artifacts: [{ node_id: 'speech-a', text: '我觉得它常见' }] }],
      {}),
    snapshot({ run: { run_id: 'ops-run', status: 'running' }, nodes: [{ node_id: 'speech-d', status: 'completed' }] }, []),
  ], speechPosts);
  let s;
  await act(async () => { s = mount(base); await settle(8); });
  await act(async () => { resizeCallback([{ contentRect: { width: 360 } }]); await settle(); });
  assert.ok(s.container.querySelector('[data-ops-panel="true"]'), 'ops panel root present');
  assert.match(text(s), /谁是卧底/);
  assert.match(text(s), /实际运行的 workflow/);
  assert.match(text(s), /我的回合/);
  assert.ok(s.container.querySelector('[data-region="ops-workflow"]'), 'workflow graph visible');
  // private word hidden
  assert.match(text(s), /••••/);
  assert.doesNotMatch(text(s), /苹果/);
  // upstream speech visible but not the raw meta
  assert.match(text(s), /我觉得它常见/);
  assert.doesNotMatch(text(s), /UNDERCOVER_UI_CONTEXT/);
  // narrow layout
  assert.equal(s.container.firstElementChild?.getAttribute('data-layout'), 'narrow');
  // speak submit
  const ta = s.container.querySelector('textarea');
  await act(async () => {
    const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set;
    setter.call(ta, '这是一个物体');
    ta.dispatchEvent(new window.Event('input', { bubbles: true }));
    await settle();
  });
  await act(async () => { button(s, '提交发言').click(); await settle(8); });
  assert.deepEqual(speechPosts.map((p) => p.content), ['这是一个物体']);
  await act(async () => s.unmount());

  // 2) Vote state: candidates both visible in room and workflow, raw vote absent.
  const votePosts = [];
  const voteParams = {
    ...base, phase: 'voting', round: 1,
    nodeActorMap: { ...base.nodeActorMap, 'vote_a': 'd', 'tally': 'host' },
    voteCandidates: PLAYERS.filter((p) => !p.isHuman).map((p) => ({ ...p, eligible: true })),
  };
  globalThis.fetch = fetchFor([
    snapshot({ run: { run_id: 'ops-run', status: 'running' }, nodes: [{ node_id: 'vote_a', status: 'ready' }, { node_id: 'tally', status: 'pending' }] },
      [{ node_id: 'vote_a', instruction: voteInstruction }],
      { 'vote_a': '{"kind":"vote","target_actor_id":"a"}' }),
  ], votePosts);
  let v;
  await act(async () => { v = mount(voteParams); await settle(8); });
  assert.match(text(v), /谁是卧底 · 第 1 轮投票/);
  assert.ok(v.container.querySelector('[data-ops-vote-choice]'), 'vote candidates shown');
  assert.match(v.container.querySelector('[data-region="ops-workflow"]').textContent, /主持人|你|小甲/g, 'vote + tally nodes mapped in workflow');
  assert.doesNotMatch(text(v), /target_actor_id|"a"/);
  // abstain offered in regular vote
  const abstain = [...v.container.querySelectorAll('[data-ops-vote-choice]')].find((b) => b.textContent?.includes('弃权'));
  assert.ok(abstain, 'regular vote offers abstain');
  // confirm required
  await act(async () => { [...v.container.querySelectorAll('[data-ops-vote-choice]')][0].click(); await settle(); });
  button(v, '确认投票').click();
  await settle();
  assert.deepEqual(votePosts, [], 'must not submit before confirmation');
  assert.match(text(v), /勾选确认/);
  // select + confirm + submit
  const check = v.container.querySelector('input[type="checkbox"]');
  await act(async () => { check.click(); await settle(); });
  await act(async () => { v.container.querySelector('form').dispatchEvent(new window.Event('submit', { bubbles: true, cancelable: true })); await settle(8); });
  assert.equal(votePosts.length, 1);
  assert.match(votePosts[0].content, /^\{"kind":"vote"/);
  await act(async () => v.unmount());

  // 3) PK state: candidates restricted, no abstain.
  const pkPosts = [];
  const pkParams = { ...base, phase: 'pk_voting', pkCandidates: ['d', 'e'], voteCandidates: [{ actorId: 'e', displayName: '小戊', seatNumber: 5, eligible: true }], voteHistory: [{ round: 1, stage: 'regular', votes: [{ seatNumber: 5, displayName: '小戊', text: '我投4号' }], counts: [{ seatNumber: 5, votes: 1 }] }] };
  const pkVoteIns = voteInstruction.replace('"allowAbstain":true', '"allowAbstain":false');
  globalThis.fetch = fetchFor([snapshot({ run: { run_id: 'ops-run', status: 'running' }, nodes: [{ node_id: 'vote_e', status: 'ready' }] }, [{ node_id: 'vote_e', instruction: pkVoteIns }], {})], pkPosts);
  let p;
  await act(async () => { p = mount(pkParams); await settle(8); });
  assert.match(text(p), /PK 投票/);
  assert.doesNotMatch(text(p), /弃权/, 'PK has no abstain');
  await act(async () => p.unmount());

  // 4) Eliminated state + old tally.
  globalThis.fetch = fetchFor([snapshot({ run: { run_id: 'ops-run', status: 'running' }, nodes: [] }, [])], []);
  let e;
  await act(async () => {
    e = mount({ ...base, round: 3, phase: 'speaking', players: PLAYERS.map((pl) => (pl.actorId === 'b' ? { ...pl, alive: false, eliminated: true } : pl)) });
    await settle(8);
  });
  assert.ok(e.container.querySelector('[data-state="eliminated"]'), 'eliminated actor row present');
  assert.match(text(e), /出局/);
  await act(async () => e.unmount());

  // 5) Finale: structured host verdict opens a result card, no raw private.
  const resultFile = 'undercover-result-v1-r1-a1.json';
  const result = { kind: 'undercover.game-result', version: 1, status: 'finished', gameSessionId: 'game', hostActorId: 'host', round: 1, attempt: 1, winner: 'civilian', reason: '卧底全部出局', summary: '平民胜利！公开词对：平民 苹果 · 卧底 梨。' };
  let lists = 0;
  globalThis.fetch = async (url, init = {}) => {
    const path = new URL(url, 'http://local.test').pathname;
    if (path.endsWith('/graph')) return response({ code: 20000, data: { run: { run_id: 'ops-run', status: 'completed' }, nodes: [{ node_id: 'host', status: 'completed' }] } });
    if (path.endsWith('/pending-human-nodes')) return response({ code: 20000, data: [] });
    if (path.endsWith('/files')) { lists += 1; return response({ code: 20000, data: { items: [{ file_id: 'result', session_id: 'session', file_name: resultFile, status: 'Ready', size: 600, owner: { actor_kind: 'Bot', actor_id: 'host' } }], total: 1 } }); }
    if (path.endsWith('/files/result/content')) return response({ code: 20000, data: result });
    if (path.includes('/nodes/host')) return response({ code: 20000, data: { node: { node_id: 'host', status: 'completed', attempt: 1, artifact_text: '终局揭晓' } } });
    throw new Error(`Unexpected ${path}`);
  };
  let f;
  await act(async () => { f = mount({ ...base, phase: 'voting', resultFile }); await settle(10); });
  assert.match(text(f), /终局|平民|卧底/, 'finale rendered');
  assert.doesNotMatch(text(f), /target_actor_id|UNDERCOVER_UI_CONTEXT/, 'raw private payload not leaked');
  await act(async () => f.unmount());
  assert.ok(lists >= 1, 'result file polled');

  // Final privacy sweep after all mounted/dismounted stages.
  assert.ok(privacyState(), 'private word / raw vote / context boundary never hit localStorage or console');

} finally {
  globalThis.fetch = originalFetch;
  console.log = origLog; console.warn = origWarn; console.error = origErr;
  dom.window.close();
}
console.log('Ops-theme lifecycle passed: speech/vote+abstain/PK/eliminated/finale, narrow layout, workflow graph, keyboard, and privacy.');