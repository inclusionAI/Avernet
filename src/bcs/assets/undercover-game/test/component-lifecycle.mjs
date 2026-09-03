import assert from 'node:assert/strict';
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { createRequire } from 'node:module';
import { JSDOM } from 'jsdom';

const dom = new JSDOM('<!doctype html><html><head></head><body></body></html>', { url: 'http://local.test/' });
globalThis.window = dom.window;
globalThis.document = dom.window.document;
globalThis.HTMLElement = dom.window.HTMLElement;
globalThis.Node = dom.window.Node;
globalThis.getComputedStyle = dom.window.getComputedStyle;
globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const require = createRequire(import.meta.url);
const { UndercoverGamePanel } = require('../dist/index.umd.js');

const params = {
  runId: 'run-component',
  groupId: 'group-component',
  sessionId: 'session-component',
  phase: 'speaking',
  round: 1,
  host: { actorId: 'host', displayName: 'Host' },
  seatOrder: ['a'],
  players: [{ actorId: 'a', displayName: 'A', isHuman: true }],
  nodeActorMap: { 'node-a': 'a', 'node-b': 'a', 'node-host': 'host' },
  currentViewerActorId: 'a',
  pollingInterval: 5,
};

function publicMessage(nodeId, text) {
  return [{
    id: `${nodeId}-${text}`,
    sequence: 1,
    content: text,
    metadata: { state_machine: { event: 'output', run_id: 'run-component', node_id: nodeId, attempt: 1 } },
  }];
}

function response(body) {
  return new Response(JSON.stringify({ code: 20000, data: body }), { status: 200, headers: { 'content-type': 'application/json' } });
}

function controlledFetch(snapshots) {
  let index = 0;
  return async (url) => {
    const path = new URL(url, 'http://local.test').pathname;
    const snapshot = snapshots[Math.min(index, snapshots.length - 1)];
    if (path.endsWith('/graph')) return response(snapshot.graph);
    if (path.endsWith('/pending-human-nodes')) return response(snapshot.pending);
    if (path.endsWith('/messages')) {
      index += 1;
      return response(snapshot.messages);
    }
    const nodeId = path.split('/').at(-1);
    return response({ node: snapshot.graph.nodes.find((node) => node.node_id === nodeId) ?? { node_id: nodeId, status: 'ready' } });
  };
}

function mountPanel(props) {
  const container = document.createElement('div');
  document.body.appendChild(container);
  const root = createRoot(container);
  return {
    container,
    render(nextProps) {
      root.render(React.createElement(UndercoverGamePanel, nextProps));
    },
    unmount() {
      root.unmount();
      container.remove();
    },
  };
}

function renderedText(panel) {
  return panel.container.textContent ?? '';
}

function findButton(panel, predicate) {
  const button = [...panel.container.querySelectorAll('button')].find(predicate);
  if (!button) throw new Error('Expected panel button was not found.');
  return button;
}

async function settle() {
  await Promise.resolve();
  await Promise.resolve();
}

const originalFetch = globalThis.fetch;
try {
  globalThis.fetch = controlledFetch([
    {
      graph: { run: { run_id: 'run-component', status: 'running' }, nodes: [{ node_id: 'node-a', status: 'running', attempt: 1 }] },
      pending: [],
      messages: publicMessage('node-a', 'old public clue'),
    },
    {
      graph: { run: { run_id: 'run-component', status: 'completed' }, nodes: [{ node_id: 'node-a', status: 'completed', attempt: 1 }] },
      pending: [],
      messages: publicMessage('node-a', 'final public clue'),
    },
  ]);
  let terminalRenderer;
  await act(async () => {
    terminalRenderer = mountPanel({ ...params, autoRefresh: false });
    terminalRenderer.render({ ...params, autoRefresh: false });
    await settle();
  });
  assert.match(renderedText(terminalRenderer), /old public clue/);
  const refreshButton = findButton(terminalRenderer, (button) => button.textContent === '↻ 刷新');
  await act(async () => {
    refreshButton.click();
    await settle();
  });
  assert.match(renderedText(terminalRenderer), /final public clue/);
  assert.match(renderedText(terminalRenderer), /阶段已完成/);

  globalThis.fetch = controlledFetch([
    {
      graph: { run: { run_id: 'run-component', status: 'running' }, nodes: [{ node_id: 'node-a', status: 'ready', attempt: 1 }] },
      pending: [{ node_id: 'node-a', instruction: 'first action' }],
      messages: [],
    },
    {
      graph: { run: { run_id: 'run-component', status: 'running' }, nodes: [{ node_id: 'node-b', status: 'ready', attempt: 1 }] },
      pending: [{ node_id: 'node-b', instruction: 'current action' }],
      messages: [],
    },
  ]);
  let actionRenderer;
  await act(async () => {
    actionRenderer = mountPanel({ ...params, autoRefresh: false });
    actionRenderer.render({ ...params, autoRefresh: false });
    await settle();
  });
  const seat = findButton(actionRenderer, (button) => button.getAttribute('aria-label') === '打开 A 详情');
  await act(async () => {
    seat.click();
    await settle();
  });
  const actionRefresh = findButton(actionRenderer, (button) => button.textContent === '↻ 刷新');
  await act(async () => {
    actionRefresh.click();
    await settle();
  });
  assert.match(renderedText(actionRenderer), /当前 HumanInput：node-b/);
  assert.doesNotMatch(renderedText(actionRenderer), /当前 HumanInput：node-a/);

  globalThis.fetch = controlledFetch([
    {
      graph: { run: { run_id: 'run-component', status: 'running' }, nodes: [{ node_id: 'node-a', status: 'running', attempt: 1 }] },
      pending: [],
      messages: publicMessage('node-a', 'before polling'),
    },
    {
      graph: { run: { run_id: 'run-component', status: 'running' }, nodes: [{ node_id: 'node-a', status: 'completed', attempt: 1 }] },
      pending: [],
      messages: publicMessage('node-a', 'after unchanged-count polling'),
    },
  ]);
  let pollingRenderer;
  await act(async () => {
    pollingRenderer = mountPanel({ ...params, autoRefresh: true });
    pollingRenderer.render({ ...params, autoRefresh: true });
    await settle();
  });
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 20));
    await settle();
  });
  assert.match(renderedText(pollingRenderer), /after unchanged-count polling/);
  assert.match(renderedText(pollingRenderer), /已发言/);
  await act(async () => pollingRenderer.unmount());

  let disabledPollingWasAborted = false;
  let initialRequestsRemaining = 3;
  globalThis.fetch = async (url, init = {}) => {
    if (initialRequestsRemaining > 0) {
      initialRequestsRemaining -= 1;
      const path = new URL(url, 'http://local.test').pathname;
      if (path.endsWith('/graph')) return response({ run: { run_id: 'run-component', status: 'running' }, nodes: [{ node_id: 'node-a', status: 'running', attempt: 1 }] });
      if (path.endsWith('/pending-human-nodes')) return response([]);
      return response(publicMessage('node-a', 'disable polling after this request'));
    }
    return new Promise((_, reject) => {
      init.signal?.addEventListener('abort', () => {
        disabledPollingWasAborted = true;
        const error = new Error('aborted');
        error.name = 'AbortError';
        reject(error);
      }, { once: true });
    });
  };
  let disableRenderer;
  await act(async () => {
    disableRenderer = mountPanel({ ...params, autoRefresh: true });
    disableRenderer.render({ ...params, autoRefresh: true });
    await settle();
  });
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 10));
    await settle();
  });
  await act(async () => {
    disableRenderer.render({ ...params, autoRefresh: false });
    await settle();
  });
  assert.equal(disabledPollingWasAborted, true);
  await act(async () => disableRenderer.unmount());

  let wasAborted = false;
  globalThis.fetch = async (_url, init = {}) => new Promise((_, reject) => {
    init.signal?.addEventListener('abort', () => {
      wasAborted = true;
      const error = new Error('aborted');
      error.name = 'AbortError';
      reject(error);
    }, { once: true });
  });
  let cleanupRenderer;
  await act(async () => {
    cleanupRenderer = mountPanel({ ...params, autoRefresh: true });
    cleanupRenderer.render({ ...params, autoRefresh: true });
    await Promise.resolve();
  });
  await act(async () => cleanupRenderer.unmount());
  assert.equal(wasAborted, true);
} finally {
  globalThis.fetch = originalFetch;
}

console.log('Component lifecycle tests passed: refreshed rendering, terminal state, current action, unchanged-count polling, auto-refresh disablement, cleanup.');
