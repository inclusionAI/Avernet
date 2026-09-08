import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { JSDOM } from 'jsdom';

const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://local.test/' });
Object.assign(globalThis, {
  window: dom.window, document: dom.window.document,
  HTMLElement: dom.window.HTMLElement, Node: dom.window.Node,
  IS_REACT_ACT_ENVIRONMENT: true,
});
const { default: React, act } = await import('react');
const { createRoot } = await import('react-dom/client');
const require = createRequire(import.meta.url);
const { StateMachineRunView, UndercoverGamePanel } = require('..');
const requests = [];
globalThis.fetch = async (url) => {
  requests.push(url);
  const runId = url.includes('/default-run/') ? 'default-run' : 'game-run';
  const data = url.endsWith('/graph') ? {
    run: { run_id: runId, status: 'completed', input: {}, created_at: 1 },
    definition: { name: 'Default workflow', nodes: [], edges: [] },
    nodes: [], edges: [],
  } : [];
  return new Response(JSON.stringify({ code: 20000, data }));
};
const container = document.createElement('div');
document.body.appendChild(container);
const root = createRoot(container);
try {
  await act(async () => {
    root.render(React.createElement(React.Fragment, null,
      React.createElement('section', { id: 'default' },
        React.createElement(StateMachineRunView, { runId: 'default-run', autoRefresh: false })),
      React.createElement('section', { id: 'game' },
        React.createElement(UndercoverGamePanel, {
          runId: 'game-run', groupId: 'group', sessionId: 'game-session',
          phase: 'speaking', round: 1, autoRefresh: false,
          host: { actorId: 'host', displayName: '主持人' },
          seatOrder: ['player'], players: [{ actorId: 'player', displayName: '玩家' }],
          nodeActorMap: {},
        })),
    ));
    await new Promise(resolve => setTimeout(resolve, 20));
  });
  assert.match(container.querySelector('#default').textContent, /default-run/);
  assert.match(container.querySelector('#game').textContent, /玩家/);
  assert.ok(requests.includes('/bcnproxy/state-machine-runs/default-run/graph'));
  assert.ok(requests.includes('/bcnproxy/state-machine-runs/game-run/graph'));
  assert.ok(requests.some(url => url.includes('/sessions/game-session/messages')));
} finally {
  await act(async () => root.unmount());
  dom.window.close();
}
console.log('Shared bundle: default workflow and game session render together with independent requests.');
