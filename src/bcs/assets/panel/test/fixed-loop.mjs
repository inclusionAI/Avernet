import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { JSDOM } from 'jsdom';

const fixture = JSON.parse(readFileSync(new URL('../../../tests/fixtures/fixed_loop_api.json', import.meta.url), 'utf8'));
const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://local.test/' });
Object.assign(globalThis, { window: dom.window, document: dom.window.document,
  HTMLElement: dom.window.HTMLElement, Node: dom.window.Node, IS_REACT_ACT_ENVIRONMENT: true });
const { default: React, act } = await import('react');
const { createRoot } = await import('react-dom/client');
const require = createRequire(import.meta.url);
const { StateMachineRunView } = require('..');
const settle = async () => { for (let i = 0; i < 3; i++) await act(async () => { await new Promise(resolve => setTimeout(resolve, 15)); }); };
let graph, pending, requests, submitted;
globalThis.fetch = async (url, options = {}) => {
  requests.push(url);
  let data;
  if (url.endsWith('/respond')) { submitted.push({ url, body: JSON.parse(options.body) }); data = fixture.node; }
  else if (url.endsWith('/graph')) data = graph;
  else if (url.endsWith('/pending-human-nodes')) data = pending;
  else data = { ...fixture.node, node: { ...fixture.node.node, attempt: 1 } };
  return new Response(JSON.stringify({ code: 20000, data }));
};

async function scenario(run) {
  graph = structuredClone(fixture.graph); pending = []; requests = []; submitted = [];
  const container = document.createElement('div'); document.body.append(container);
  const root = createRoot(container);
  const mount = async (expanded = true) => {
    await act(async () => root.render(React.createElement(StateMachineRunView, { runId: 'run-1', autoRefresh: false })));
    await settle();
    const toggle = [...container.querySelectorAll('button')].find(button => button.textContent === '展开执行图');
    if (expanded && toggle) await act(async () => toggle.click());
  };
  try { await run(container, mount); }
  finally { await act(async () => root.unmount()); container.remove(); }
}
try {
  await scenario(async (container, mount) => {
    graph = JSON.parse(readFileSync(new URL('../../../tests/fixtures/fixed_loop_logical_view.json', import.meta.url), 'utf8'));
    await mount(false);
    assert.equal(container.querySelectorAll('[data-loop-container]').length, 1);
    assert.equal(container.querySelectorAll('[data-loop-id="revision_rounds"]').length, 2);
    assert.equal(container.querySelectorAll('[data-loop-route="continue"]').length, 1);
    assert.ok(container.querySelector('[data-loop-route="break"]'));
    const nodeRects = [...container.querySelectorAll('[data-loop-id]')].map(node => node.querySelector('rect'));
    const bodyCenters = nodeRects.map(rect => Number(rect.getAttribute('x')) + Number(rect.getAttribute('width')) / 2);
    assert.equal(bodyCenters[0], bodyCenters[1], 'body nodes share the main axis');
    assert.equal(container.querySelector('[data-loop-route="break"]').dataset.edgeTarget, 'polish');
    assert.equal(container.querySelector('[data-loop-route="exhausted"]').dataset.edgeTarget, 'rewrite');
    const select = container.querySelector('[data-loop-controls] select');
    assert.equal(select.value, '2');
    assert.deepEqual([...select.options].map(option => option.value), ['1', '2']);
    assert.doesNotMatch(select.textContent, /\d\s*\/\s*3/, 'round selection must not imply all rounds are required');
    await act(async () => { select.value = '1'; select.dispatchEvent(new dom.window.Event('change', { bubbles: true })); });
    assert.equal(container.querySelector('[data-loop-id]').dataset.loopIteration, '1');
    graph.nodes.filter(node => node.execution?.iteration === 2).forEach(node => { node.status = 'completed'; node.outcome = node.execution?.definition_node_id === 'review' ? 'revise' : 'complete'; });
    graph.nodes.find(node => node.node_id === 'draft-3').status = 'running';
    await act(async () => container.querySelector('[aria-label="Refresh state machine run"]').click());
    await settle();
    assert.equal(select.value, '1', 'refresh preserves explicit history selection');
    await act(async () => [...container.querySelectorAll('button')].find(button => button.textContent === '回到当前执行').click());
    assert.equal(select.value, '3');
    assert.equal(container.querySelector('[data-loop-id]').dataset.loopIteration, '3');
    await act(async () => container.querySelector('[data-loop-id]').dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true })));
    await settle();
    assert.ok(requests.some(url => url.endsWith('/nodes/draft-3')), 'detail uses the selected execution ID');
  });
  await scenario(async (container, mount) => {
    graph = JSON.parse(readFileSync(new URL('../../../tests/fixtures/fixed_loop_logical_view.json', import.meta.url), 'utf8'));
    await mount();
    assert.equal(container.querySelectorAll('[data-loop-container]').length, 2);
    assert.equal(container.querySelectorAll('[data-loop-id]').length, 4);
    assert.doesNotMatch(container.querySelector('[aria-label="State machine graph"]').textContent, /重写待完善稿件|润色通过稿件|汇总最终稿/,
      'unentered branches must not become disconnected roots when future executions are hidden');
  });
  await scenario(async (container, mount) => {
    graph.nodes[0].outcome = 'done';
    graph.nodes[1].status = graph.nodes[2].status = 'skipped';
    graph.nodes[3].status = 'running';
    await mount(false);
    assert.equal(container.querySelector('select').options.length, 1);
    assert.match(container.querySelector('[data-loop-controls]').textContent, /done/);
    assert.equal(container.querySelector('[data-loop-route="break"]').dataset.edgeState, 'executed');
  });
  await scenario(async (container, mount) => {
    graph.nodes[2].status = 'completed'; graph.nodes[2].outcome = 'again'; graph.nodes[3].status = 'running';
    await mount(false);
    assert.match(container.textContent, /exhausted/);
    assert.equal(container.querySelector('select').value, '3');
    assert.equal(container.querySelector('[data-loop-route="exhausted"]').dataset.edgeState, 'executed');
    assert.equal(container.querySelector('[data-loop-route="continue"]').dataset.edgeState, 'skipped');
  });
  await scenario(async (container, mount) => {
    graph.nodes[1].status = 'retry_scheduled'; graph.nodes[1].attempt = 2;
    graph.nodes[2].status = 'pending';
    await mount(false);
    assert.equal(container.querySelector('select').value, '2', 'retry stays in the same iteration');
    graph.run.status = 'aborted'; graph.nodes[1].status = graph.nodes[2].status = 'skipped';
    await act(async () => container.querySelector('[aria-label="Refresh state machine run"]').click());
    await settle();
    assert.match(container.querySelector('[data-loop-controls]').textContent, /已取消/);
    assert.equal(container.querySelector('select').value, '2', 'cancelled future nodes do not advance the round');
  });
  await scenario(async (container, mount) => {
    graph.nodes = graph.nodes.filter(node => !node.execution || node.execution.iteration === 1);
    graph.nodes[0].execution.max_iterations = graph.loops.rounds.max_iterations = 1;
    graph.edges = [{ source: graph.nodes[0].node_id, target: graph.nodes[1].node_id, outcome: 'again',
      loop_route: { kind: 'exhausted', logical_outcome: 'exhausted' } }];
    await mount(false);
    assert.equal(container.querySelector('[data-loop-route="continue"]'), null);
    assert.equal(container.querySelectorAll('[data-loop-id]').length, 1);
  });
  await scenario(async (container, mount) => {
    graph.nodes.forEach((node, i) => { node.status = i < 2 ? 'completed' : i === 2 ? 'running' : 'pending'; });
    graph.nodes[2].kind = 'human_input';
    pending = structuredClone(fixture.pending_later);
    await mount(false);
    assert.equal(container.querySelector('[data-loop-id]').dataset.loopIteration, '3');
    assert.match(container.textContent, /等待人工/);
    const select = container.querySelector('[data-loop-controls] select');
    await act(async () => { select.value = '1'; select.dispatchEvent(new dom.window.Event('change', { bubbles: true })); });
    await act(async () => container.querySelector('[data-loop-id]').dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true })));
    await settle();
    assert.equal(container.querySelector('textarea'), null, 'history cannot respond to a different active iteration');
    await act(async () => [...container.querySelectorAll('button')].find(button => button.textContent === '回到当前执行').click());
    await act(async () => container.querySelector('[data-loop-id]').dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true })));
    await settle();
    const input = container.querySelector('textarea');
    assert.ok(input);
    await act(async () => {
      Object.getOwnPropertyDescriptor(dom.window.HTMLTextAreaElement.prototype, 'value').set.call(input, 'current round only');
      input.dispatchEvent(new dom.window.Event('input', { bubbles: true }));
    });
    await act(async () => input.closest('form').dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true })));
    await settle();
    assert.deepEqual(submitted, [{ url: '/bcnproxy/state-machine-runs/run-1/nodes/historical-work-3/respond', body: { content: 'current round only' } }]);
  });
  await scenario(async (container, mount) => {
    graph.nodes[2].status = 'completed'; graph.nodes[2].outcome = 'again'; graph.nodes[3].status = 'running';
    await mount();
    assert.equal(container.querySelectorAll('[data-loop-id="rounds"]').length, 3);
    assert.equal(container.querySelectorAll('[data-loop-container]').length, 3);
    assert.doesNotMatch(container.textContent, /第 \d+ 轮/);
    const exhausted = container.querySelector('[data-loop-route="exhausted"]');
    assert.equal(exhausted.dataset.edgeState, 'executed');
    const lastBreak = container.querySelector('[data-loop-route="break"][data-edge-source="historical-work-3"]');
    assert.equal(lastBreak.dataset.edgeState, 'skipped', 'same target does not select the other outcome');
    assert.match(container.textContent, /exhausted/);
    assert.match(exhausted.parentNode.querySelector('title').textContent, /实际 outcome: again/);
    assert.notEqual(exhausted.parentNode.querySelector('text').getAttribute('y'), lastBreak.parentNode.querySelector('text').getAttribute('y'));
    await act(async () => container.querySelector('[data-loop-iteration="3"]').dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true })));
    await settle();
    assert.ok(requests.includes('/bcnproxy/state-machine-runs/run-1/nodes/historical-work-3'));
    const details = document.querySelector('[aria-label="Loop execution details"]');
    assert.match(details.textContent, /Logical node: work/);
    assert.match(details.textContent, /Execution ID: historical-work-3/);
    assert.match(details.textContent, /Retry attempt: 1/);
  });
  await scenario(async (container, mount) => {
    graph.nodes[0].outcome = 'done'; graph.nodes[1].status = graph.nodes[2].status = 'skipped'; graph.nodes[3].status = 'running';
    await mount();
    assert.equal(container.querySelector('[data-loop-route="break"][data-edge-source="historical-work-1"]').dataset.edgeState, 'executed');
    assert.equal(container.querySelector('[data-loop-route="continue"][data-edge-source="historical-work-1"]'), null);
    assert.equal(container.querySelector('[data-loop-iteration="3"]'), null);
  });
  for (const entered of [0, 5, 10]) await scenario(async (container, mount) => {
    const base = structuredClone(graph.nodes[0]);
    const publish = graph.nodes[3];
    graph.loops.rounds.max_iterations = 100;
    graph.nodes = Array.from({ length: 100 }, (_, index) => ({ ...base, node_id: `opaque-game-${index + 1}`,
      execution: { ...base.execution, iteration: index + 1, max_iterations: 100 },
      status: index < entered ? 'completed' : entered ? 'skipped' : 'pending',
      outcome: index === entered - 1 ? 'done' : index < entered ? 'again' : undefined,
      started_at: undefined, completed_at: entered ? 42 : undefined, attempt: 0 }));
    graph.definition.initial_nodes = [graph.nodes[0].node_id];
    graph.edges = graph.nodes.flatMap((node, index) => [
      { source: node.node_id, target: index < 99 ? graph.nodes[index + 1].node_id : publish.node_id, outcome: 'again',
        loop_route: { kind: index < 99 ? 'continue' : 'exhausted', logical_outcome: index < 99 ? 'again' : 'exhausted' } },
      { source: node.node_id, target: publish.node_id, outcome: 'done', loop_route: { kind: 'break', logical_outcome: 'done' } },
    ]);
    graph.nodes.push(publish);
    await mount(false);
    const select = container.querySelector('[data-loop-controls] select');
    assert.equal(select?.options.length ?? 0, entered, 'history excludes unentered nodes even with skip timestamps');
    assert.equal(container.querySelectorAll('[data-loop-id]').length, 1);
    assert.equal(container.querySelector('[data-loop-id]').dataset.loopIteration, String(entered || 1));
    assert.doesNotMatch(container.querySelector('[data-loop-id]').textContent, /第|轮|100/);
    await act(async () => [...container.querySelectorAll('button')].find(button => button.textContent === '展开执行图').click());
    assert.equal(container.querySelectorAll('[data-loop-id]').length, entered || 1);
    assert.equal(container.querySelectorAll('[data-loop-container]').length, entered || 1);
    assert.equal(container.querySelector('[data-loop-iteration="100"]'), null);
    assert.equal(container.querySelectorAll('[data-loop-route="continue"]').length, Math.max(entered - 1, 0));
    if (entered) {
      assert.equal(container.querySelector('[data-loop-route="exhausted"]'), null);
      await act(async () => container.querySelector(`[data-loop-iteration="${entered}"]`).dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true })));
      await settle();
      assert.ok(requests.some(url => url.endsWith(`/nodes/opaque-game-${entered}`)));
    }
  });
  await scenario(async (container, mount) => {
    graph.run.status = 'aborted';
    graph.nodes.forEach(node => { node.status = 'skipped'; node.attempt = 0; });
    graph.nodes[1].started_at = 0;
    await mount(false);
    const select = container.querySelector('[data-loop-controls] select');
    assert.deepEqual([...select.options].map(option => option.value), ['2'], 'a cancelled execution with a zero start timestamp is retained');
    await act(async () => [...container.querySelectorAll('button')].find(button => button.textContent === '展开执行图').click());
    assert.equal(container.querySelectorAll('[data-loop-id]').length, 1);
    assert.equal(container.querySelector('[data-loop-id]').dataset.loopIteration, '2');
  });
  for (const first of [true, false]) await scenario(async (container, mount) => {
    const index = first ? 0 : 2;
    graph.nodes.forEach((node, i) => { node.status = i < index ? 'completed' : i === index ? 'running' : 'pending'; });
    graph.nodes[index].kind = 'human_input';
    pending = structuredClone(first ? fixture.pending_first : fixture.pending_later);
    if (!first) pending[0].upstream_artifacts = [{ node_id: 'historical-work-2', text: 'result-2' }];
    await mount();
    await act(async () => container.querySelector(`[data-loop-iteration="${index + 1}"]`).dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true })));
    await settle();
    const contexts = container.querySelectorAll('[aria-label="Loop context"]');
    assert.ok(contexts.length > 0);
    assert.match(contexts[0].textContent, first ? /暂无上次执行结果/ : /上次执行结果.*again.*result-2/);
    if (!first) assert.equal((container.textContent.match(/result-2/g) || []).length, 1, 'context is not duplicated in upstream artifacts');
    const input = container.querySelector('textarea'); assert.ok(input);
    await act(async () => {
      Object.getOwnPropertyDescriptor(dom.window.HTMLTextAreaElement.prototype, 'value').set.call(input, 'review current iteration');
      input.dispatchEvent(new dom.window.Event('input', { bubbles: true }));
    });
    await act(async () => input.closest('form').dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true })));
    await settle();
    assert.deepEqual(submitted, [{ url: `/bcnproxy/state-machine-runs/run-1/nodes/historical-work-${index + 1}/respond`, body: { content: 'review current iteration' } }]);
  });
  await scenario(async (container, mount) => {
    graph.definition.graph_mode = 'acyclic'; delete graph.definition.execution_graph_mode;
    graph.nodes.forEach(node => { delete node.execution; }); graph.edges.forEach(edge => { delete edge.loop_route; });
    await mount();
    assert.equal(container.querySelector('[data-loop-id]'), null);
    assert.equal(container.querySelector('[aria-label="Loop context"]'), null);
    assert.doesNotMatch(container.textContent, /Work · 第 \d 轮|上限兜底/);
  });
} finally { dom.window.close(); }
console.log('Fixed Loop panel: logical/expanded views, actual history (100 limit, 0/5/10 entered), single execution, retry/cancel, exhausted/break, opaque detail IDs, first/later Human context and exact response target, v1 compatibility passed.');
