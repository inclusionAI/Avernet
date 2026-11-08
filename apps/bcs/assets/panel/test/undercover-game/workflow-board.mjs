import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { JSDOM } from 'jsdom';
import { workflowFixtures } from './fixtures/workflow-graphs.mjs';

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
const resizeCallbacks = [];
globalThis.ResizeObserver = class {
  constructor(callback) { resizeCallbacks.push(callback); }
  observe() {}
  disconnect() {}
};
window.ResizeObserver = globalThis.ResizeObserver;
const resizeAll = (width) => { for (const callback of resizeCallbacks) callback([{ contentRect: { width } }]); };

const ReactModule = await import('react');
const React = ReactModule.default;
const { act } = ReactModule;
const { createRoot } = await import('react-dom/client');
const require = createRequire(import.meta.url);
const api = require('../../dist/index.umd.js');
const { UndercoverGamePanel, buildWorkflowBoard, workflowNodeKind, workflowStatusLabel } = api;

const response = (body, status = 200) => new Response(JSON.stringify({ code: 20000, data: body }), { status, headers: { 'content-type': 'application/json' } });
const settle = async () => { await Promise.resolve(); await Promise.resolve(); await new Promise((resolve) => setTimeout(resolve, 0)); await Promise.resolve(); };
const text = (panel) => panel.container.textContent ?? '';
function mount(props, width = 760) {
  const container = document.createElement('div');
  document.body.appendChild(container);
  Object.defineProperty(container, 'getBoundingClientRect', { value: () => ({ width, height: 1000, top: 0, left: 0, right: width, bottom: 1000, x: 0, y: 0, toJSON() {} }) });
  const root = createRoot(container);
  root.render(React.createElement(UndercoverGamePanel, props));
  return { container, render: (next) => root.render(React.createElement(UndercoverGamePanel, next)), unmount: () => { root.unmount(); container.remove(); } };
}
function fetchFor(graph, pending = []) {
  return async (url) => {
    const path = new URL(url, 'http://local.test').pathname;
    if (path.endsWith('/graph')) {
      if (graph instanceof Error) return response({ message: graph.message }, 500);
      return response(graph);
    }
    if (path.endsWith('/pending-human-nodes')) return response(pending);
    if (path.includes('/nodes/')) return response({ node: { node_id: path.split('/').at(-1), status: 'completed', attempt: 1 } });
    return response({}, 404);
  };
}
function fixtureProps(fixture, extra = {}) {
  const { resultFile, ...params } = fixture.params;
  void resultFile;
  return { ...params, autoRefresh: false, apiBaseUrl: '/api/v1/collaboration', ...extra };
}
function boardOf(panel) {
  const board = panel.container.querySelector('[data-region="workflow-board"]');
  assert.ok(board, 'workflow board is rendered inside the panel');
  return board;
}
function boardNodes(panel) {
  return [...boardOf(panel).querySelectorAll('[data-workflow-node]')];
}
function edgePairs(panel) {
  return [...boardOf(panel).querySelectorAll('[data-workflow-edge]')].map((edge) => `${edge.getAttribute('data-edge-source')}->${edge.getAttribute('data-edge-target')}`).sort();
}
function graphEdgePairs(graph) {
  return (graph.edges ?? []).map((edge) => `${edge.source}->${edge.target}`).sort();
}
function statusOf(panel, nodeId) {
  const node = boardNodes(panel).find((item) => item.getAttribute('data-node-id') === nodeId);
  assert.ok(node, `node ${nodeId} is rendered`);
  return node.getAttribute('data-node-status');
}
function rankOf(panel, nodeId) {
  const node = boardNodes(panel).find((item) => item.getAttribute('data-node-id') === nodeId);
  assert.ok(node, `node ${nodeId} is rendered`);
  return Number(node.getAttribute('data-node-rank'));
}

const originalFetch = globalThis.fetch;
try {
  // ---- Pure graph projection: structural fields only, deterministic kinds/labels ----
  assert.equal(workflowNodeKind('speak_3'), 'speak');
  assert.equal(workflowNodeKind('vote_12'), 'vote');
  assert.equal(workflowNodeKind('vote_start'), 'vote-start');
  assert.equal(workflowNodeKind('collect'), 'collect');
  assert.equal(workflowNodeKind('tally'), 'tally');
  assert.equal(workflowNodeKind('mystery'), 'unknown');
  assert.equal(workflowStatusLabel('retry_scheduled'), '等待重试');
  assert.equal(workflowStatusLabel('aborted'), '已中止');
  assert.equal(workflowStatusLabel('weird_status'), '状态未知');
  const missing = buildWorkflowBoard(undefined, { runId: 'run-x', nodeActorMap: {}, players: [], host: { actorId: 'host', displayName: '主持人' } });
  assert.equal(missing.available, false);
  assert.deepEqual(missing.degradeReasons, ['missing-graph']);
  const aborted = buildWorkflowBoard(
    { run: { run_id: 'run-aborted', status: 'aborted' }, nodes: [{ node_id: 'speak_1', status: 'aborted' }], edges: [] },
    { runId: 'run-aborted', nodeActorMap: { speak_1: 'a' }, players: [{ actorId: 'a', displayName: '甲', seatNumber: 1 }], host: { actorId: 'host', displayName: '主持人' } },
  );
  assert.equal(aborted.runStatusLabel, '已中止');
  assert.equal(aborted.nodes[0]?.statusLabel, '已中止');
  assert.equal(aborted.nodes[0]?.tone, 'aborted');
  const unknownStatus = buildWorkflowBoard(
    { run: { run_id: 'run-unknown', status: 'mystery' }, nodes: [{ node_id: 'speak_1', status: 'mystery' }], edges: [] },
    { runId: 'run-unknown', nodeActorMap: { speak_1: 'a' }, players: [{ actorId: 'a', displayName: '甲', seatNumber: 1 }], host: { actorId: 'host', displayName: '主持人' } },
  );
  assert.equal(unknownStatus.runStatusLabel, '运行状态未知');
  assert.equal(unknownStatus.nodes[0]?.statusLabel, '状态未知');
  assert.ok(unknownStatus.degradeReasons.includes('unknown-status'));
  const unmapped = buildWorkflowBoard(
    { run: { run_id: 'run-unmapped', status: 'running' }, nodes: [{ node_id: 'speak_1', status: 'pending' }], edges: [] },
    { runId: 'run-unmapped', nodeActorMap: {}, players: [], host: { actorId: 'host', displayName: '主持人' } },
  );
  assert.deepEqual(unmapped.unknownNodeIds, ['speak_1']);
  assert.ok(unmapped.degradeReasons.includes('unknown-node'));
  assert.match(unmapped.notice ?? '', /未映射/);
  const cyclic = buildWorkflowBoard(
    { run: { run_id: 'run-cycle', status: 'running' }, nodes: [{ node_id: 'speak_1', status: 'running' }, { node_id: 'speak_2', status: 'pending' }], edges: [{ source: 'speak_1', target: 'speak_2' }, { source: 'speak_2', target: 'speak_1' }] },
    { runId: 'run-cycle', nodeActorMap: { speak_1: 'a', speak_2: 'b' }, players: [{ actorId: 'a', displayName: '甲', seatNumber: 1 }, { actorId: 'b', displayName: '乙', seatNumber: 2 }], host: { actorId: 'host', displayName: '主持人' } },
  );
  assert.ok(cyclic.degradeReasons.includes('cyclic-graph'));
  assert.equal(cyclic.lanes.length, 1);
  assert.match(cyclic.notice ?? '', /循环连线/);
  const selfLoop = buildWorkflowBoard(
    { run: { run_id: 'run-loop', status: 'running' }, nodes: [{ node_id: 'speak_1', status: 'running' }], edges: [{ source: 'speak_1', target: 'speak_1' }] },
    { runId: 'run-loop', nodeActorMap: { speak_1: 'a' }, players: [{ actorId: 'a', displayName: '甲', seatNumber: 1 }], host: { actorId: 'host', displayName: '主持人' } },
  );
  assert.ok(selfLoop.degradeReasons.includes('cyclic-graph'));
  assert.ok(!selfLoop.degradeReasons.includes('unknown-edge'), 'self-loop is a cycle, not an unknown endpoint');
  assert.equal(selfLoop.edges.length, 0);
  const unknownEdge = buildWorkflowBoard(
    { run: { run_id: 'run-edge', status: 'running' }, nodes: [{ node_id: 'speak_1', status: 'running' }], edges: [{ source: 'speak_1', target: 'ghost_node' }] },
    { runId: 'run-edge', nodeActorMap: { speak_1: 'a' }, players: [{ actorId: 'a', displayName: '甲', seatNumber: 1 }], host: { actorId: 'host', displayName: '主持人' } },
  );
  assert.ok(unknownEdge.degradeReasons.includes('unknown-edge'));
  assert.equal(unknownEdge.edges.length, 0);
  const duplicateEdges = buildWorkflowBoard(
    { run: { run_id: 'run-dup', status: 'running' }, nodes: [{ node_id: 'speak_1', status: 'running' }, { node_id: 'collect', status: 'pending' }], edges: [{ source: 'speak_1', target: 'collect' }, { source: 'speak_1', target: 'collect' }] },
    { runId: 'run-dup', nodeActorMap: { speak_1: 'a', collect: 'host' }, players: [{ actorId: 'a', displayName: '甲', seatNumber: 1 }], host: { actorId: 'host', displayName: '主持人' } },
  );
  assert.equal(duplicateEdges.edges.length, 1, 'duplicate edges are deduplicated');


  // ---- Sequential speech: real chain, host collect last, exact fixture edges ----
  {
    const fixture = workflowFixtures.speakSequential;
    globalThis.fetch = fetchFor(fixture.graph);
    let panel;
    await act(async () => { panel = mount(fixtureProps(fixture)); await settle(); });
    assert.equal(boardOf(panel).getAttribute('data-workflow-collapsed'), 'true');
    await act(async () => { boardOf(panel).querySelector('[data-region="workflow-toggle"]').click(); await settle(); });
    const board = boardOf(panel);
    assert.equal(board.getAttribute('data-run-id'), fixture.graph.run.run_id);
    assert.equal(board.getAttribute('data-run-status'), fixture.graph.run.status?.toLowerCase());
    const concrete = fixture.graph.nodes.filter((node) => node.node_id.startsWith('speak_'));
    assert.deepEqual(boardNodes(panel).map((node) => node.getAttribute('data-node-id')), fixture.graph.nodes.map((node) => node.node_id));
    assert.deepEqual(edgePairs(panel), graphEdgePairs(fixture.graph));
    assert.ok(fixture.graph.nodes.some((node) => node.node_id === 'collect' && node.status === 'pending'));
    assert.equal(rankOf(panel, 'collect') > Math.max(...concrete.map((node) => rankOf(panel, node.node_id))), true, 'collect is ranked after every speak node');
    assert.equal(board.querySelector('[data-final-node="true"]').getAttribute('data-node-id'), 'collect');
    for (const node of fixture.graph.nodes) assert.equal(statusOf(panel, node.node_id), node.status);
    assert.match(text(panel), /本阶段真实流程/);
    assert.equal(board.querySelector('[data-region="workflow-toggle"]').getAttribute('aria-expanded'), 'true');
    await act(async () => panel.unmount());
  }

  // ---- Parallel vote: vote_start then same-rank vote_N, tally last; no vote-to-vote edges ----
  {
    const fixture = workflowFixtures.voteParallel;
    globalThis.fetch = fetchFor(fixture.graph);
    let panel;
    await act(async () => { panel = mount(fixtureProps(fixture)); await settle(); });
    assert.equal(boardOf(panel).getAttribute('data-workflow-collapsed'), 'true');
    await act(async () => { boardOf(panel).querySelector('[data-region="workflow-toggle"]').click(); await settle(); });
    const voteIds = fixture.graph.nodes.filter((node) => node.node_id.startsWith('vote_') && node.node_id !== 'vote_start').map((node) => node.node_id);
    assert.equal(statusOf(panel, 'vote_start'), 'completed');
    const voteRanks = voteIds.map((id) => rankOf(panel, id));
    assert.equal(new Set(voteRanks).size, 1, 'vote_N nodes are the same parallel rank');
    assert.equal(rankOf(panel, 'tally') > Math.max(...voteRanks), true, 'tally is ranked after every vote_N');
    assert.equal(new Set(voteIds.map((id) => statusOf(panel, id))).size >= 2, true, 'fixture exposes mixed observable vote states');
    assert.deepEqual(edgePairs(panel), graphEdgePairs(fixture.graph));
    assert.equal(edgePairs(panel).some((edge) => /^vote_\d+->vote_\d+$/.test(edge)), false, 'no vote-to-vote edges');
    assert.equal(boardOf(panel).querySelector('[data-final-node="true"]').getAttribute('data-node-id'), 'tally');
    await act(async () => panel.unmount());
  }

  // ---- Tally last: all ballots complete, host tally running ----
  {
    const fixture = workflowFixtures.voteTallyLast;
    globalThis.fetch = fetchFor(fixture.graph);
    let panel;
    await act(async () => { panel = mount(fixtureProps(fixture)); await settle(); });
    assert.equal(boardOf(panel).getAttribute('data-workflow-collapsed'), 'true');
    await act(async () => { boardOf(panel).querySelector('[data-region="workflow-toggle"]').click(); await settle(); });
    assert.equal(statusOf(panel, 'tally'), 'running');
    assert.match(boardOf(panel).querySelector('[data-node-id="tally"]').textContent, /进行中/);
    for (const node of fixture.graph.nodes.filter((item) => item.node_id.startsWith('vote_') && item.node_id !== 'vote_start')) {
      assert.equal(statusOf(panel, node.node_id), 'completed');
    }
    await act(async () => panel.unmount());
  }

  // ---- PK speech/vote: only the nodes the real PK graph contains ----
  for (const key of ['pkSpeak', 'pkVote']) {
    const fixture = workflowFixtures[key];
    globalThis.fetch = fetchFor(fixture.graph);
    let panel;
    await act(async () => { panel = mount(fixtureProps(fixture)); await settle(); });
    assert.equal(boardOf(panel).getAttribute('data-workflow-collapsed'), 'true');
    await act(async () => { boardOf(panel).querySelector('[data-region="workflow-toggle"]').click(); await settle(); });
    assert.deepEqual(boardNodes(panel).map((node) => node.getAttribute('data-node-id')), fixture.graph.nodes.map((node) => node.node_id), `${key} renders exactly the graph nodes`);
    assert.deepEqual(edgePairs(panel), graphEdgePairs(fixture.graph), `${key} renders exactly the graph edges`);
    assert.ok(fixture.graph.nodes.some((node) => node.node_id === 'collect' || node.node_id === 'tally'));
    await act(async () => panel.unmount());
  }

  // ---- Failed/retry nodes expose only public state; never node.error ----
  {
    const fixture = workflowFixtures.failed;
    globalThis.fetch = fetchFor(fixture.graph);
    let panel;
    await act(async () => { panel = mount(fixtureProps(fixture)); await settle(); });
    assert.equal(boardOf(panel).getAttribute('data-workflow-collapsed'), 'true');
    await act(async () => { boardOf(panel).querySelector('[data-region="workflow-toggle"]').click(); await settle(); });
    const failed = boardNodes(panel).find((node) => node.getAttribute('data-node-status') === 'failed');
    assert.ok(failed, 'failed node remains visible');
    assert.match(failed.textContent, /失败/);
    assert.equal(boardOf(panel).getAttribute('data-run-status'), 'failed');
    assert.match(boardOf(panel).textContent ?? '', /失败/);
    assert.ok(!text(panel).includes('PRIVATE_ERROR_SHOULD_NOT_RENDER'), 'raw node.error is never rendered');
    assert.ok(!text(panel).includes('artifact_text') && !text(panel).includes('instruction'));
    await act(async () => panel.unmount());
  }
  {
    const fixture = workflowFixtures.retry;
    globalThis.fetch = fetchFor(fixture.graph);
    let panel;
    await act(async () => { panel = mount(fixtureProps(fixture)); await settle(); });
    assert.equal(boardOf(panel).getAttribute('data-workflow-collapsed'), 'true');
    await act(async () => { boardOf(panel).querySelector('[data-region="workflow-toggle"]').click(); await settle(); });
    const retryNode = boardNodes(panel).find((node) => node.getAttribute('data-node-status') === 'retry_scheduled');
    assert.ok(retryNode);
    assert.match(retryNode.textContent, /等待重试/);
    assert.match(retryNode.textContent, /第 2 次尝试/);
    assert.equal(retryNode.getAttribute('data-node-attempt'), '2');
    await act(async () => panel.unmount());
  }

  // ---- Degradations: missing edges, stale run invalidates old nodes, unknown nodes, missing graph ----
  {
    const fixture = workflowFixtures.missingEdges;
    globalThis.fetch = fetchFor(fixture.graph);
    let panel;
    await act(async () => { panel = mount(fixtureProps(fixture)); await settle(); });
    assert.equal(boardOf(panel).getAttribute('data-workflow-collapsed'), 'true');
    await act(async () => { boardOf(panel).querySelector('[data-region="workflow-toggle"]').click(); await settle(); });
    assert.equal(boardNodes(panel).length, fixture.graph.nodes.length);
    assert.equal(edgePairs(panel).length, 0);
    assert.match(boardOf(panel).getAttribute('data-workflow-degrade') ?? '', /missing-edges/);
    assert.match(text(panel), /未提供连线/);
    await act(async () => panel.unmount());
  }
  {
    const fixture = workflowFixtures.staleRun;
    globalThis.fetch = fetchFor(fixture.graph);
    let panel;
    await act(async () => { panel = mount(fixtureProps(fixture)); await settle(); });
    assert.equal(boardOf(panel).getAttribute('data-workflow-collapsed'), 'true');
    await act(async () => { boardOf(panel).querySelector('[data-region="workflow-toggle"]').click(); await settle(); });
    assert.equal(boardNodes(panel).length, 0, 'graph for another run renders no nodes');
    assert.match(boardOf(panel).getAttribute('data-workflow-degrade') ?? '', /stale-run/);
    assert.match(text(panel), /已隐藏上一张运行图/);
    await act(async () => panel.unmount());
  }
  {
    const fixture = workflowFixtures.unknownNode;
    globalThis.fetch = fetchFor(fixture.graph);
    let panel;
    await act(async () => { panel = mount(fixtureProps(fixture)); await settle(); });
    assert.equal(boardOf(panel).getAttribute('data-workflow-collapsed'), 'true');
    await act(async () => { boardOf(panel).querySelector('[data-region="workflow-toggle"]').click(); await settle(); });
    const unknown = boardNodes(panel).find((node) => node.getAttribute('data-node-id') === 'vote_ghost');
    assert.ok(unknown, 'unmapped ghost node is explicitly rendered');
    assert.equal(unknown.getAttribute('data-node-mapped'), 'false');
    assert.match(unknown.textContent ?? '', /未映射/);
    assert.match(unknown.textContent ?? '', /vote_ghost/, 'unknown node keeps its public node id');
    assert.match(text(panel), /节点类型未识别/);
    assert.match(boardOf(panel).getAttribute('data-workflow-degrade') ?? '', /unknown-kind/);
    await act(async () => panel.unmount());
  }
  {
    globalThis.fetch = fetchFor(new Error('graph unavailable'));
    let panel;
    await act(async () => { panel = mount(fixtureProps(workflowFixtures.speakSequential)); await settle(); });
    await act(async () => { boardOf(panel).querySelector('[data-region="workflow-toggle"]').click(); await settle(); });
    assert.equal(boardNodes(panel).length, 0);
    assert.match(boardOf(panel).getAttribute('data-workflow-degrade') ?? '', /missing-graph/);
    assert.match(text(panel), /尚未获取本阶段运行图/);
    await act(async () => panel.unmount());
  }

  // ---- Run switch clears the old graph and never shows stale node state ----
  {
    const fixture = workflowFixtures.speakSequential;
    const oldGraph = fixture.graph;
    globalThis.fetch = fetchFor(oldGraph);
    let panel;
    await act(async () => { panel = mount(fixtureProps(fixture)); await settle(); });
    assert.equal(boardOf(panel).getAttribute('data-workflow-collapsed'), 'true');
    await act(async () => { boardOf(panel).querySelector('[data-region="workflow-toggle"]').click(); await settle(); });
    const oldIds = boardNodes(panel).map((node) => node.getAttribute('data-node-id'));
    assert.ok(oldIds.length > 0);
    await act(async () => { panel.render(fixtureProps(fixture, { runId: 'run-switched-away' })); await settle(); });
    const newIds = boardNodes(panel).map((node) => node.getAttribute('data-node-id'));
    assert.equal(newIds.length, 0, 'old nodes disappear while the new run graph is pending');
    for (const id of oldIds) assert.ok(!text(panel).includes(id), `old node ${id} is gone from the board`);
    await act(async () => panel.unmount());
  }

  {
    const fixture = workflowFixtures.speakSequential;
    const runA = fixture.graph;
    const runB = { ...runA, run: { ...runA.run, run_id: 'run-switched-to' }, nodes: runA.nodes.map((node) => ({ ...node, status: 'pending' })), edges: runA.edges };
    globalThis.fetch = async (url) => {
      const path = new URL(url, 'http://local.test').pathname;
      if (path.endsWith('/graph')) return response(path.includes('run-switched-to') ? runB : runA);
      if (path.endsWith('/pending-human-nodes')) return response([]);
      if (path.includes('/nodes/')) return response({ node: { node_id: path.split('/').at(-1), status: 'completed', attempt: 1 } });
      return response({}, 404);
    };
    let panel;
    await act(async () => { panel = mount(fixtureProps(fixture)); await settle(); });
    assert.equal(boardOf(panel).getAttribute('data-workflow-collapsed'), 'true');
    await act(async () => { boardOf(panel).querySelector('[data-region="workflow-toggle"]').click(); await settle(); });
    assert.ok(boardNodes(panel).length > 0);
    await act(async () => { panel.render(fixtureProps(fixture, { runId: 'run-switched-to' })); await settle(); });
    assert.equal(boardOf(panel).getAttribute('data-workflow-collapsed'), 'true');
    await act(async () => { boardOf(panel).querySelector('[data-region="workflow-toggle"]').click(); await settle(); });
    assert.equal(boardOf(panel).getAttribute('data-run-id'), 'run-switched-to');
    assert.deepEqual(boardNodes(panel).map((node) => node.getAttribute('data-node-id')), runB.nodes.map((node) => node.node_id));
    assert.ok(boardNodes(panel).every((node) => node.getAttribute('data-node-status') === 'pending'));
    await act(async () => panel.unmount());
  }

  // ---- Narrow action: board starts collapsed but is keyboard-expandable; run switch resets it ----
  {
    const fixture = workflowFixtures.speakSequential;
    const instruction = '[UNDERCOVER_UI_CONTEXT_V1]' + JSON.stringify({ action: 'speech', round: 1, seatNumber: 1, word: '合成词', maxChars: 5, forbidOwnWord: true }) + '[/UNDERCOVER_UI_CONTEXT_V1]';
    const pending = [{ node_id: 'speak_1', attempt: 1, instruction, timeout_deadline_ms: Date.now() + 60000 }];
    globalThis.fetch = fetchFor(fixture.graph, pending);
    let panel;
    await act(async () => { panel = mount(fixtureProps(fixture), 320); await settle(); });
    await act(async () => { resizeAll(320); await settle(); });
    const board = boardOf(panel);
    assert.equal(board.getAttribute('data-workflow-collapsed'), 'true');
    assert.equal(board.getAttribute('data-workflow-expanded'), 'false');
    assert.equal(boardNodes(panel).length, 0, 'collapsed board renders no nodes');
    const toggle = board.querySelector('[data-region="workflow-toggle"]');
    assert.ok(toggle, 'narrow action shows a keyboard-reachable toggle');
    assert.equal(toggle.getAttribute('aria-expanded'), 'false');
    assert.equal(toggle.textContent?.trim(), '查看流程');
    assert.ok(toggle.getAttribute('aria-controls'));
    assert.equal(panel.container.querySelector(`#${toggle.getAttribute('aria-controls')}`) !== null, true, 'aria-controls target exists');
    toggle.focus();
    assert.equal(document.activeElement, toggle, 'toggle is focusable');
    await act(async () => { toggle.click(); await settle(); });
    assert.equal(boardOf(panel).getAttribute('data-workflow-expanded'), 'true');
    assert.equal(boardOf(panel).getAttribute('data-workflow-collapsed'), 'false');
    assert.ok(boardNodes(panel).length > 0, 'expanded board renders the real graph nodes');
    assert.equal(toggle.getAttribute('aria-expanded'), 'true');
    assert.equal(toggle.textContent?.trim(), '收起流程');
    assert.deepEqual(edgePairs(panel), graphEdgePairs(fixture.graph));
    await act(async () => { panel.render(fixtureProps(fixture, { runId: 'run-switched-away' })); await settle(); });
    await act(async () => { resizeAll(320); await settle(); });
    assert.equal(boardOf(panel).getAttribute('data-workflow-expanded'), 'false', 'run switch does not reuse the old expanded state');
    assert.equal(boardOf(panel).getAttribute('data-workflow-collapsed'), 'true');
    assert.equal(boardNodes(panel).length, 0);
    await act(async () => panel.unmount());
  }

  // ---- Label coverage for ready/pending/completed/skipped and run aborted ----
  {
    const fixture = workflowFixtures.speakSequential;
    globalThis.fetch = fetchFor(fixture.graph);
    let panel;
    await act(async () => { panel = mount(fixtureProps(fixture)); await settle(); });
    assert.equal(boardOf(panel).getAttribute('data-workflow-collapsed'), 'true');
    await act(async () => { boardOf(panel).querySelector('[data-region="workflow-toggle"]').click(); await settle(); });
    const labels = Object.fromEntries(boardNodes(panel).map((node) => [node.getAttribute('data-node-status'), node.textContent]));
    assert.match(labels.completed ?? '', /已完成/);
    assert.match(labels.running ?? '', /进行中/);
    assert.match(labels.ready ?? '', /待执行/);
    assert.match(labels.pending ?? '', /待开始/);
    await act(async () => panel.unmount());
    const skipped = buildWorkflowBoard(
      { run: { run_id: 'run-skipped', status: 'running' }, nodes: [{ node_id: 'speak_1', status: 'skipped' }], edges: [] },
      { runId: 'run-skipped', nodeActorMap: { speak_1: 'a' }, players: [{ actorId: 'a', displayName: '甲', seatNumber: 1 }], host: { actorId: 'host', displayName: '主持人' } },
    );
    assert.equal(skipped.nodes[0]?.statusLabel, '已跳过');
    assert.equal(skipped.nodes[0]?.tone, 'skipped');
  }

  // ---- Privacy scan: injected artifacts/instructions/words/errors/votes never reach the board ----
  {
    const fixture = workflowFixtures.speakSequential;
    const malicious = {
      ...fixture.graph,
      nodes: fixture.graph.nodes.map((node) => ({
        ...node,
        artifact_text: 'SECRET_ARTIFACT_TEXT',
        instruction: 'SECRET_INSTRUCTION',
        error: 'PRIVATE_ERROR_SHOULD_NOT_RENDER',
        display_name: 'SECRET_DISPLAY_NAME',
        word: 'SECRET_WORD',
        vote_target: 'SECRET_TARGET',
      })),
    };
    globalThis.fetch = fetchFor(malicious);
    let panel;
    await act(async () => { panel = mount(fixtureProps({ ...fixture, graph: malicious })); await settle(); });
    await act(async () => { boardOf(panel).querySelector('[data-region="workflow-toggle"]').click(); await settle(); });
    const boardText = boardOf(panel).textContent ?? '';
    for (const secret of ['SECRET_ARTIFACT_TEXT', 'SECRET_INSTRUCTION', 'PRIVATE_ERROR_SHOULD_NOT_RENDER', 'SECRET_DISPLAY_NAME', 'SECRET_WORD', 'SECRET_TARGET']) {
      assert.ok(!boardText.includes(secret), `${secret} must not reach the workflow board`);
    }
    const attrs = boardNodes(panel).map((node) => [...node.attributes].map((attr) => attr.value).join(' ')).join(' ');
    assert.ok(!/SECRET_|PRIVATE_ERROR/.test(attrs), 'board attributes contain no artifact/instruction/error values');
    const projected = buildWorkflowBoard(malicious, {
      runId: malicious.run.run_id,
      nodeActorMap: fixture.params.nodeActorMap,
      players: fixture.params.players,
      host: fixture.params.host,
    });
    assert.ok(!/SECRET_|PRIVATE_ERROR/.test(JSON.stringify(projected)), 'projected workflow view carries no private node payloads');
    await act(async () => panel.unmount());
  }
} finally {
  globalThis.fetch = originalFetch;
}
console.log('Workflow board tests passed: serial speech, parallel vote with tally last, PK graphs, failed/retry states, missing graph/edges, unknown nodes, run switching, and privacy.');
