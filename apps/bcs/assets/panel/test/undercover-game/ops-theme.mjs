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
const { UndercoverGameOpsPanel, buildRunnerGraph, OPS_PALETTE } = require('../../dist/index.umd.js');

// --- design tokens -----------------------------------------------------------
// The ops theme must not ship an independent literal palette: every surface,
// accent and status colour derives from the shared shadcn semantic tokens.
{
  const palette = OPS_PALETTE.obsidian;
  for (const [name, value] of Object.entries(palette)) {
    assert.match(value, /^hsl\(var\(--[a-z-]+/, `${name} must derive from a shared semantic token, got ${value}`);
  }
  assert.equal(palette.accent, 'hsl(var(--primary, 221.7 100% 62%))', 'accent follows --primary');
  assert.equal(palette.bg, 'hsl(var(--background, 240 10% 3.9%))', 'surface follows --background');
  assert.equal(palette.text, 'hsl(var(--foreground, 0 0% 98%))', 'text follows --foreground');
  assert.equal(palette.done, 'hsl(var(--success, 142 71% 45%))', 'status follows --success');
  assert.equal(palette.bad, 'hsl(var(--destructive, 0 72.2% 50.6%))', 'error follows --destructive');
}

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
  const workflowPanel = s.container.querySelector('[data-region="ops-workflow"]').parentElement;
  assert.equal(getComputedStyle(workflowPanel).flexShrink, '0', 'scene cards keep their content height so controls remain reachable by scrolling');
  const opsRoot = s.container.querySelector('[data-ops-panel="true"]');
  const opsStyle = getComputedStyle(opsRoot);
  assert.equal(opsStyle.getPropertyValue('--muted'), '', 'ops theme must not shadow the host raw-HSL --muted token');
  assert.equal(opsStyle.getPropertyValue('--ops-muted-text'), OPS_PALETTE.obsidian.muted, 'muted text uses a panel-local alias');
  assert.equal(opsStyle.getPropertyValue('--panel-alt'), OPS_PALETTE.obsidian.panelAlt, 'textarea surface still derives from the shared --muted token');
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
  const voteFetch = fetchFor([
    snapshot({ run: { run_id: 'ops-run', status: 'running' }, nodes: [{ node_id: 'vote_a', status: 'ready' }, { node_id: 'tally', status: 'pending' }] },
      [{ node_id: 'vote_a', instruction: voteInstruction }],
      { 'vote_a': '{"kind":"vote","target_actor_id":"a"}' }),
  ], votePosts);
  let releaseVote;
  let notifyVoteStarted;
  const voteStarted = new Promise((resolve) => { notifyVoteStarted = resolve; });
  globalThis.fetch = async (url, init = {}) => {
    const path = new URL(url, 'http://local.test').pathname;
    if (init.method === 'POST' && path.endsWith('/respond')) {
      votePosts.push(JSON.parse(init.body));
      notifyVoteStarted();
      await new Promise((resolve) => { releaseVote = resolve; });
      return env({ data: { accepted: true }, enveloped: true });
    }
    return voteFetch(url, init);
  };
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
  // switching the target must clear the confirmation so it cannot carry over
  await act(async () => { [...v.container.querySelectorAll('[data-ops-vote-choice]')][1].click(); await settle(); });
  assert.equal(v.container.querySelector('input[type="checkbox"]').checked, false, 'confirmation resets when the vote target changes');
  await act(async () => { v.container.querySelector('input[type="checkbox"]').click(); await settle(); });
  await act(async () => {
    v.container.querySelector('form').dispatchEvent(new window.Event('submit', { bubbles: true, cancelable: true }));
    await voteStarted;
    await settle();
  });
  assert.equal(votePosts.length, 1);
  assert.match(votePosts[0].content, /^\{"kind":"vote"/);
  const selectedBallot = [...v.container.querySelectorAll('[data-ops-vote-choice]')].find((b) => b.getAttribute('aria-checked') === 'true');
  const selectedText = selectedBallot.textContent;
  const ballotControls = [...v.container.querySelectorAll('[data-ops-vote-choice]')];
  assert.ok(ballotControls.every((control) => control.disabled), 'candidate and abstention controls freeze while the vote is submitting');
  assert.equal(v.container.querySelector('input[type="checkbox"]').disabled, true, 'vote confirmation freezes while submitting');
  assert.equal(button(v, '提交中…').disabled, true, 'vote submit button reports the pending request');
  await act(async () => {
    ballotControls.find((control) => control !== selectedBallot && !control.textContent.includes('弃权')).click();
    v.container.querySelector('input[type="checkbox"]').click();
    await settle();
  });
  assert.equal(selectedBallot.getAttribute('aria-checked'), 'true', 'slow submission cannot change the displayed ballot');
  assert.equal(selectedBallot.textContent, selectedText);
  assert.equal(v.container.querySelector('input[type="checkbox"]').checked, true, 'slow submission cannot change confirmation');
  await act(async () => { releaseVote(); await settle(8); });
  assert.equal(votePosts.length, 1, 'locked controls do not create a second vote');
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

  // 4b) Host public broadcasts survive in the Ops view and honour showHostOutput.
  const hostBroadcast = '主持人播报：本轮计票完成，请把票投给最可疑的人。';
  const hostParams = { ...base, phase: 'voting', nodeActorMap: { ...base.nodeActorMap, 'tally-note': 'host' } };
  const hostSnapshot = () => snapshot({ run: { run_id: 'ops-run', status: 'running' }, nodes: [{ node_id: 'tally-note', status: 'completed' }] }, [], { 'tally-note': hostBroadcast });
  globalThis.fetch = fetchFor([hostSnapshot()], []);
  let hb;
  await act(async () => { hb = mount(hostParams); await settle(8); });
  assert.match(hb.container.querySelector('[data-region="ops-host-broadcast"]')?.textContent ?? '', /主持人播报：本轮计票完成/, 'host broadcast rendered');
  await act(async () => hb.unmount());
  globalThis.fetch = fetchFor([hostSnapshot()], []);
  let hh;
  await act(async () => { hh = mount({ ...hostParams, display: { showHostOutput: false } }); await settle(8); });
  assert.equal(hh.container.querySelector('[data-region="ops-host-broadcast"]'), null, 'host broadcast hidden by showHostOutput=false');
  assert.doesNotMatch(text(hh), /本轮计票完成/, 'host text not rendered when hidden');
  await act(async () => hh.unmount());

  let hostRefreshFails = false;
  const hostFetch = fetchFor([hostSnapshot()], []);
  globalThis.fetch = async (url, init = {}) => {
    const path = new URL(url, 'http://local.test').pathname;
    if (hostRefreshFails && path.endsWith('/graph')) return response({ message: 'refresh unavailable' }, 503);
    return hostFetch(url, init);
  };
  let staleHost;
  await act(async () => { staleHost = mount(hostParams); await settle(8); });
  await act(async () => {
    staleHost.render({ ...hostParams, display: { showHostOutput: false } });
    await settle(8);
  });
  hostRefreshFails = true;
  await act(async () => { button(staleHost, '刷新').click(); await settle(8); });
  assert.match(text(staleHost), /刷新失败/, 'stale host fixture retained its last successful snapshot');
  assert.equal(staleHost.container.querySelector('[data-region="ops-host-broadcast"]'), null, 'stale host content honors the current showHostOutput=false');
  assert.doesNotMatch(text(staleHost), /本轮计票完成/, 'failed refresh cannot reveal cached host text');
  await act(async () => staleHost.unmount());

  // 4c) Accumulated public speech history stays reachable in the Ops view.
  const historyParams = {
    ...voteParams,
    publicHistory: [
      { round: 1, stage: 'regular', speeches: [{ actorId: 'a', seatNumber: 1, displayName: '小甲', text: '第一轮线索：会在忙碌的一天用到。' }] },
      { round: 2, stage: 'pk', speeches: [{ actorId: 'c', seatNumber: 3, displayName: '小丙', text: '第二轮线索：和水果有关。' }] },
    ],
  };
  globalThis.fetch = fetchFor([
    snapshot({ run: { run_id: 'ops-run', status: 'running' }, nodes: [{ node_id: 'vote_a', status: 'completed' }] }, [], { 'vote_a': '已投票' }),
  ], []);
  let hp;
  await act(async () => { hp = mount(historyParams); await settle(8); });
  const historyRegion = hp.container.querySelector('[data-region="ops-public-history"]');
  assert.ok(historyRegion, 'public history panel rendered');
  assert.match(historyRegion.textContent, /第 1 轮 · 常规发言/, 'rounds grouped by round/stage');
  assert.match(historyRegion.textContent, /第一轮线索：会在忙碌的一天用到。/, 'earlier round speech stays readable');
  assert.match(historyRegion.textContent, /第二轮线索：和水果有关。/, 'pk round speech stays readable');
  await act(async () => hp.unmount());

  // 4d) Live current-round clues wrap without relying on a pending speech form.
  const liveClue = '这是需要完整展示的实时线索，不会在忙碌的一天里突然消失。';
  globalThis.fetch = fetchFor([
    snapshot({ run: { run_id: 'ops-run', status: 'running' }, nodes: [{ node_id: 'speech-a', status: 'completed' }, { node_id: 'speech-b', status: 'running' }] }, [], { 'speech-a': liveClue }),
  ], []);
  let live;
  await act(async () => { live = mount(base); await settle(8); });
  assert.equal(live.container.querySelector('textarea'), null, 'live clue is observable after the human speech form is gone');
  const liveRow = live.container.querySelector('[data-actor-id="a"]');
  const liveClueNode = liveRow.lastElementChild;
  assert.equal(liveClueNode.textContent, liveClue, 'live node detail supplies the complete clue');
  assert.notEqual(liveClueNode.style.whiteSpace, 'nowrap', 'live clue is not restricted to one ellipsized line');
  assert.equal(liveClueNode.style.overflowWrap, 'anywhere', 'long live clues wrap at narrow roster widths');
  await act(async () => live.unmount());

  // 4e) Reopened ballots explain invalidation only after vote_start completes in a running vote.
  const reopenedNotice = '上一轮投票已作废，请根据最新发言重新投票。';
  const openingCases = [
    { name: 'visible', phase: 'voting', runStatus: 'running', voteStartStatus: 'completed', visible: true },
    { name: 'vote_start pending', phase: 'voting', runStatus: 'running', voteStartStatus: 'ready', visible: false, waiting: true },
    { name: 'run completed', phase: 'voting', runStatus: 'completed', voteStartStatus: 'completed', visible: false },
    { name: 'not voting', phase: 'speaking', runStatus: 'running', voteStartStatus: 'completed', visible: false },
  ];
  for (const testCase of openingCases) {
    globalThis.fetch = fetchFor([
      snapshot({ run: { run_id: 'ops-run', status: testCase.runStatus }, nodes: [{ node_id: 'vote_start', status: testCase.voteStartStatus }] }, []),
    ], []);
    let opening;
    await act(async () => {
      opening = mount({ ...voteParams, phase: testCase.phase, attempt: 2, openingAnnouncement: reopenedNotice });
      await settle(8);
    });
    if (testCase.visible) {
      assert.match(text(opening), /上一轮投票已作废/, 'reopened vote renders the referee announcement');
      assert.equal(opening.container.querySelectorAll('[data-region="ops-vote-opening"]').length, 1, 'opening announcement has one dedicated status region');
    } else {
      assert.doesNotMatch(text(opening), /上一轮投票已作废/, `${testCase.name} must not expose a stale opening announcement`);
      if (testCase.waiting) assert.match(text(opening), /主持人正在开启投票/, 'running vote explains that vote_start is still pending');
    }
    await act(async () => opening.unmount());
  }

  // 4f) A recovery message does not revalidate a conflicted action; only a successful refresh does.
  {
    const conflictPosts = [];
    const recoveryActions = [];
    let graphReads = 0;
    let refreshAvailable = false;
    globalThis.fetch = async (url, init = {}) => {
      const path = new URL(url, 'http://local.test').pathname;
      if (init.method === 'POST' && path.endsWith('/respond')) {
        conflictPosts.push(JSON.parse(init.body));
        return response({ message: 'already handled' }, 409);
      }
      if (path.endsWith('/graph')) {
        graphReads += 1;
        if (graphReads > 1 && !refreshAvailable) return response({ message: 'refresh unavailable' }, 503);
        return env({ data: { run: { run_id: 'ops-run', status: 'running' }, nodes: [{ node_id: 'speech-d', status: 'ready' }] }, enveloped: true });
      }
      if (path.endsWith('/pending-human-nodes')) {
        return env({ data: [{ node_id: 'speech-d', attempt: 1, instruction: speechInstruction, upstream_artifacts: [] }], enveloped: true });
      }
      throw new Error(`Unexpected ${path}`);
    };
    let conflicted;
    await act(async () => {
      conflicted = mount({ ...base, onAction: async (action) => { recoveryActions.push(action); } });
      await settle(8);
    });
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set;
      setter.call(conflicted.container.querySelector('textarea'), '冲突前的发言');
      conflicted.container.querySelector('textarea').dispatchEvent(new window.Event('input', { bubbles: true }));
      await settle();
      conflicted.container.querySelector('form').dispatchEvent(new window.Event('submit', { bubbles: true, cancelable: true }));
      await settle(8);
    });
    assert.equal(conflictPosts.length, 1);
    assert.equal(conflicted.container.querySelector('form'), null, 'conflict disables the cached action while refresh fails');
    await act(async () => { button(conflicted, '卡住了？告诉主持人').click(); await settle(8); });
    assert.deepEqual(recoveryActions, [{ type: 'send_message', content: '卡住了' }]);
    const unlockedForm = conflicted.container.querySelector('form');
    if (unlockedForm) {
      await act(async () => {
        unlockedForm.dispatchEvent(new window.Event('submit', { bubbles: true, cancelable: true }));
        await settle(8);
      });
    }
    assert.equal(conflictPosts.length, 1, 'recovery delivery must not unlock resubmission of the stale action');
    assert.equal(conflicted.container.querySelector('form'), null, 'stale guard survives a successful recovery message');
    refreshAvailable = true;
    await act(async () => { button(conflicted, '刷新').click(); await settle(8); });
    assert.ok(conflicted.container.querySelector('form'), 'a successful refresh revalidates and unlocks the pending action');
    await act(async () => conflicted.unmount());
  }

  // 4f2) Recovery delivery is single-flight and its footer control stays disabled while pending.
  {
    const recoveryActions = [];
    let releaseRecovery;
    const delivery = new Promise((resolve) => { releaseRecovery = resolve; });
    globalThis.fetch = fetchFor([snapshot({ run: { run_id: 'ops-run', status: 'running' }, nodes: [] }, [])], []);
    let recovering;
    await act(async () => {
      recovering = mount({ ...base, onAction: (action) => { recoveryActions.push(action); return delivery; } });
      await settle(8);
    });
    const recoveryButton = button(recovering, '卡住了？告诉主持人');
    await act(async () => {
      recoveryButton.click();
      recoveryButton.click();
      await settle();
    });
    assert.deepEqual(recoveryActions, [{ type: 'send_message', content: '卡住了' }], 'rapid confirmation sends one recovery request');
    assert.equal(recoveryButton.disabled, true, 'recovery button is disabled during delivery');
    assert.match(recoveryButton.textContent, /正在通知/);
    await act(async () => { recoveryButton.click(); await settle(); });
    assert.equal(recoveryActions.length, 1, 'pending delivery cannot be duplicated');
    await act(async () => { releaseRecovery(); await settle(8); });
    assert.equal(recoveryButton.disabled, false, 'recovery button unlocks after delivery settles');
    await act(async () => recovering.unmount());
  }

  // 4g) A late finale suppresses the pending snapshot from the earlier parallel reads.
  {
    const lateResultFile = 'undercover-result-v1-r1-a1.json';
    const lateResult = { kind: 'undercover.game-result', version: 1, status: 'finished', gameSessionId: 'game', hostActorId: 'host', round: 1, attempt: 1, winner: 'civilian', reason: '卧底全部出局', summary: '终局已经发布，旧操作必须关闭。' };
    const finaleRacePosts = [];
    let resultReady = false;
    globalThis.fetch = async (url, init = {}) => {
      const path = new URL(url, 'http://local.test').pathname;
      if (init.method === 'POST' && path.endsWith('/respond')) {
        finaleRacePosts.push(JSON.parse(init.body));
        return env({ data: { accepted: true }, enveloped: true });
      }
      if (path.endsWith('/graph')) return env({ data: { run: { run_id: 'ops-run', status: 'running' }, nodes: [{ node_id: 'speech-d', status: 'ready' }] }, enveloped: true });
      if (path.endsWith('/pending-human-nodes')) return env({ data: [{ node_id: 'speech-d', attempt: 1, instruction: speechInstruction, upstream_artifacts: [] }], enveloped: true });
      if (path.endsWith('/files')) {
        const items = resultReady ? [{ file_id: 'late-result', session_id: 'session', file_name: lateResultFile, status: 'Ready', size: 600, owner: { actor_kind: 'Bot', actor_id: 'host' } }] : [];
        return response({ code: 20000, data: { items, total: items.length } });
      }
      if (path.endsWith('/files/late-result/content')) return response({ code: 20000, data: lateResult });
      throw new Error(`Unexpected ${path}`);
    };
    let finaleRace;
    await act(async () => { finaleRace = mount({ ...base, resultFile: lateResultFile }); await settle(8); });
    assert.ok(finaleRace.container.querySelector('form'), 'pending action is available before the finale is published');
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set;
      setter.call(finaleRace.container.querySelector('textarea'), '终局前的草稿');
      finaleRace.container.querySelector('textarea').dispatchEvent(new window.Event('input', { bubbles: true }));
      await settle();
    });
    resultReady = true;
    await act(async () => { button(finaleRace, '刷新').click(); await settle(8); });
    assert.ok(finaleRace.container.querySelector('[data-region="ops-finale-summary"]'), 'late authenticated finale is visible');
    const staleActionForm = finaleRace.container.querySelector('form');
    if (staleActionForm) {
      await act(async () => {
        staleActionForm.dispatchEvent(new window.Event('submit', { bubbles: true, cancelable: true }));
        await settle(8);
      });
    }
    assert.equal(finaleRacePosts.length, 0, 'a finale blocks submission from the earlier pending snapshot');
    assert.equal(finaleRace.container.querySelector('form'), null, 'pending form is suppressed when the finale is available');
    assert.equal(finaleRace.container.querySelector('[data-ops-panel="true"]').getAttribute('data-action-active'), 'false');
    await act(async () => finaleRace.unmount());
  }

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
  assert.ok(f.container.querySelector('[data-region="ops-finale-summary"]'), 'public finale summary rendered next to the verdict');
  assert.match(text(f), /公开词对：平民 苹果 · 卧底 梨/, 'public word pair and recap visible');
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