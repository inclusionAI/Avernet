import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { JSDOM } from 'jsdom';

const dom = new JSDOM('<!doctype html><html><body></body></html>', {
  url: 'http://local.test/',
});
Object.assign(globalThis, {
  window: dom.window,
  document: dom.window.document,
  HTMLElement: dom.window.HTMLElement,
  Node: dom.window.Node,
  IS_REACT_ACT_ENVIRONMENT: true,
});
const { default: React, act } = await import('react');
const { createRoot } = await import('react-dom/client');
const require = createRequire(import.meta.url);
const {
  CollaborationDefinitionCard,
  CollaborationDefinitionPreview,
  CollaborationDefinitionSnapshotError,
  decodeCollaborationDefinitionSnapshot,
  decodeCollaborationDefinitionValidationResult,
} = require('..');
const vectors = JSON.parse(
  readFileSync(
    new URL(
      '../../../tests/fixtures/collaboration-definition-snapshot/vectors.json',
      import.meta.url,
    ),
    'utf8',
  ),
);
const fixedLoopPreview = JSON.parse(
  readFileSync(
    new URL('../../../tests/fixtures/fixed_loop_preview.json', import.meta.url),
    'utf8',
  ),
);
const longLoopPreview = structuredClone(fixedLoopPreview);
const loopNode = longLoopPreview.graph.nodes[0];
const publishNode = longLoopPreview.graph.nodes.at(-1);
longLoopPreview.summary.nodes = 21;
longLoopPreview.graph.loops.rounds.max_iterations = 20;
longLoopPreview.graph.nodes = [
  ...Array.from({ length: 20 }, (_, index) => ({
    ...structuredClone(loopNode),
    node_id: `historical-work-${index + 1}`,
    execution: {
      ...loopNode.execution,
      iteration: index + 1,
      max_iterations: 20,
    },
  })),
  publishNode,
];
longLoopPreview.graph.edges = Array.from({ length: 20 }, (_, index) => {
  const iteration = index + 1;
  const source = `historical-work-${iteration}`;
  return [
    iteration < 20
      ? {
          source,
          outcome: 'again',
          target: `historical-work-${iteration + 1}`,
          loop_route: { kind: 'continue', logical_outcome: 'again' },
        }
      : {
          source,
          outcome: 'again',
          target: 'publish',
          loop_route: { kind: 'exhausted', logical_outcome: 'exhausted' },
        },
    {
      source,
      outcome: 'done',
      target: 'publish',
      loop_route: { kind: 'break', logical_outcome: 'done' },
    },
  ];
}).flat();
const snapshot = vectors.valid[0].snapshot;

for (const vector of vectors.valid) {
  assert.deepEqual(
    decodeCollaborationDefinitionSnapshot(vector.snapshot),
    vector.snapshot,
  );
}
for (const vector of vectors.invalid) {
  assert.throws(
    () => decodeCollaborationDefinitionSnapshot(vector.params),
    (error) =>
      error instanceof CollaborationDefinitionSnapshotError &&
      error.code === vector.error,
    vector.name,
  );
}

const opened = [];
const closed = [];
const focused = [];
window.aixGetLibraryCdn = (name) =>
  name === 'bcsPanel' ? '/bcnproxy/assets/panel/index.umd.js' : undefined;
window.aixBridge = {
  openPanelTab(tab) {
    opened.push(tab);
  },
  closePanelTab(id) {
    closed.push(id);
  },
  focusPanelTab(id) {
    focused.push(id);
  },
};

const cardContainer = document.createElement('div');
cardContainer.dataset.messageId = 'persisted-message-1';
document.body.appendChild(cardContainer);
const cardRoot = createRoot(cardContainer);
await act(async () => {
  cardRoot.render(
    React.createElement(CollaborationDefinitionCard, {
      params: { data: { params: snapshot } },
    }),
  );
});
assert.equal(opened.length, 0, 'card must not auto-open the side panel');
await act(async () => {
  cardContainer.querySelector('button').click();
});
assert.equal(opened.length, 1);
assert.equal(opened[0].type, 'umd');
assert.equal(opened[0].params.entry, 'CollaborationDefinitionPreview');
assert.equal(opened[0].params.cdn, '/bcnproxy/assets/panel/index.umd.js');
assert.deepEqual(opened[0].params.data.snapshot, snapshot);
assert.equal(opened[0].params.data.sourceMessageId, 'persisted-message-1');
assert.deepEqual(focused, [opened[0].id]);
await act(async () => {
  cardContainer.querySelector('button').click();
});
assert.equal(opened.length, 2);
assert.equal(opened[1].id, opened[0].id, 'same snapshot focuses the same tab');
assert.deepEqual(focused, [opened[0].id, opened[0].id]);
await act(async () => {
  cardRoot.render(
    React.createElement(CollaborationDefinitionCard, {
      params: {
        data: {
          params: { ...snapshot, definitionYaml: 'name: conflicting-content\n' },
        },
      },
    }),
  );
});
await act(async () => {
  cardContainer.querySelector('button').click();
});
assert.equal(opened.length, 2, 'identity conflict must not replace the tab');
assert.match(cardContainer.textContent, /拒绝覆盖现有预览/);
await act(async () => cardRoot.unmount());
assert.deepEqual(closed, [opened[0].id]);

const fallbackOpened = [];
const fallbackClosed = [];
window.aixBridge = {
  openPanelTab(tab) {
    fallbackOpened.push(tab);
  },
  closePanelTab(id) {
    fallbackClosed.push(id);
  },
};
const fallbackContainer = document.createElement('div');
fallbackContainer.dataset.messageId = 'persisted-message-focus-fallback';
document.body.appendChild(fallbackContainer);
const fallbackRoot = createRoot(fallbackContainer);
await act(async () => {
  fallbackRoot.render(
    React.createElement(CollaborationDefinitionCard, {
      params: { data: { params: snapshot } },
    }),
  );
});
await act(async () => fallbackContainer.querySelector('button').click());
await act(async () => fallbackContainer.querySelector('button').click());
assert.equal(fallbackOpened.length, 2);
assert.deepEqual(
  fallbackClosed,
  [fallbackOpened[0].id],
  'an existing tab is recreated when the host has no public focus method',
);
await act(async () => fallbackRoot.unmount());
assert.deepEqual(fallbackClosed, [fallbackOpened[0].id, fallbackOpened[0].id]);

assert.throws(
  () =>
    decodeCollaborationDefinitionValidationResult({
      valid: true,
      errors: [],
      warnings: [],
      summary: { participants: 1, nodes: 'one' },
    }),
  /summary.nodes/,
);

const requests = [];
globalThis.fetch = async (url, options) => {
  requests.push({ url, options });
  return new Response(
    JSON.stringify({
      code: 20000,
      message: 'OK',
      request_id: 'request-1',
      data: {
        valid: true,
        errors: [],
        warnings: [
          {
            code: 'VALIDATION_ONLY_FEATURE',
            path: 'runtime.state_machine.version',
            message: 'execution is disabled',
          },
        ],
        summary: {
          participants: 1,
          nodes: 1,
          initial_nodes: ['write'],
          final_output_node: 'write',
        },
        participants: [
          {
            binding: 'writer',
            display_name: '撰稿人',
            required: true,
            assigned: false,
          },
        ],
        graph: {
          graph_mode: 'acyclic',
          nodes: [
            {
              node_id: 'write',
              display_name: '撰写内容',
              kind: 'bot_task',
              assignee: { type: 'bot_binding', binding: 'writer' },
              final_output: true,
              judge: false,
            },
          ],
          edges: [],
        },
      },
    }),
    { status: 200, headers: { 'Content-Type': 'application/json' } },
  );
};
const previewContainer = document.createElement('div');
document.body.appendChild(previewContainer);
const previewRoot = createRoot(previewContainer);
await act(async () => {
  previewRoot.render(
    React.createElement(CollaborationDefinitionPreview, {
      snapshot,
      sourceMessageId: 'persisted-message-1',
    }),
  );
  await new Promise((resolve) => setTimeout(resolve, 30));
});
assert.equal(requests.length, 1);
assert.equal(requests[0].url, '/bcnproxy/collaboration/definitions/validate');
assert.deepEqual(JSON.parse(requests[0].options.body), {
  definition_yaml: snapshot.definitionYaml,
});
assert.match(previewContainer.textContent, /定义校验通过/);
assert.match(previewContainer.textContent, /VALIDATION_ONLY_FEATURE/);
assert.match(previewContainer.textContent, /撰稿人/);
assert.ok(
  previewContainer.querySelector('[aria-label="Collaboration definition graph"]'),
);
assert.equal(previewContainer.querySelector('pre'), null, 'legacy snapshots also open in preview');
const previewTabs = [...previewContainer.querySelectorAll('[role="tab"]')];
await act(async () => previewTabs.find(tab => tab.textContent === '源码').click());
assert.equal(previewContainer.querySelector('pre').textContent, snapshot.definitionYaml);
assert.equal(previewContainer.querySelector('[aria-label="Collaboration definition graph"]'), null);
await act(async () => previewTabs.find(tab => tab.textContent === '预览').click());
assert.ok(previewContainer.querySelector('[aria-label="Collaboration definition graph"]'));
assert.equal(requests.length, 1, 'tab changes must not resubmit inline YAML validation');
await act(async () => previewRoot.unmount());

const emptyContainer = document.createElement('div');
document.body.appendChild(emptyContainer);
const emptyRoot = createRoot(emptyContainer);
await act(async () => {
  emptyRoot.render(
    React.createElement(CollaborationDefinitionPreview, {
      snapshot: {
        ...snapshot,
        snapshotId: '33333333-3333-4333-8333-333333333333',
        definitionYaml: '   \n',
      },
      sourceMessageId: 'persisted-message-2',
    }),
  );
  await new Promise((resolve) => setTimeout(resolve, 10));
});
assert.equal(requests.length, 1, 'blank YAML must not call validation');
assert.match(emptyContainer.textContent, /YAML 为空/);
await act(async () => emptyRoot.unmount());

// The legacy inline path follows the same host base and keeps its YAML intact.
const hostRoot = createRoot(previewContainer);
for (const [hostProps, expectedUrl] of [
  [{ apiBaseUrl: '/api/v1/collaboration/', baseUrl: '/ignored' }, '/api/v1/collaboration/definitions/validate'],
  [{ baseUrl: '/custom-bcs///' }, '/custom-bcs/collaboration/definitions/validate'],
  [{ data: { apiBaseUrl: '/data-bcs' } }, '/data-bcs/collaboration/definitions/validate'],
  [{ data: { baseUrl: '/data-alias' } }, '/data-alias/collaboration/definitions/validate'],
]) {
  const start = requests.length;
  await act(async () => {
    hostRoot.render(React.createElement(CollaborationDefinitionPreview, { snapshot, ...hostProps }));
    await new Promise(resolve => setTimeout(resolve, 30));
  });
  assert.equal(requests.length, start + 1, 'changing only host configuration must revalidate');
  assert.equal(requests.at(-1).url, expectedUrl);
  assert.deepEqual(JSON.parse(requests.at(-1).options.body), { definition_yaml: snapshot.definitionYaml });
  assert.match(previewContainer.textContent, /定义校验通过/);
}
await act(async () => hostRoot.unmount());

globalThis.fetch = async () =>
  new Response(JSON.stringify(longLoopPreview), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
const loopContainer = document.createElement('div');
document.body.appendChild(loopContainer);
const loopRoot = createRoot(loopContainer);
await act(async () => {
  loopRoot.render(
    React.createElement(CollaborationDefinitionPreview, {
      snapshot: {
        ...snapshot,
        snapshotId: '44444444-4444-4444-8444-444444444444',
      },
      sourceMessageId: 'persisted-message-loop',
    }),
  );
  await new Promise((resolve) => setTimeout(resolve, 30));
});
const nodeY = (nodeId) =>
  Number(
    loopContainer
      .querySelector(`[data-node-id="${nodeId}"] rect`)
      .getAttribute('y'),
  );
assert.equal(loopContainer.querySelectorAll('[data-node-id]').length, 2, 'twenty iterations have one logical body');
assert.ok(nodeY('historical-work-1') < nodeY('publish'));
assert.match(loopContainer.querySelector('[data-loop-container="rounds"]').textContent, /20 轮/);
const continueEdges = Array.from(
  loopContainer.querySelectorAll('[data-edge-route="continue"]'),
);
assert.equal(continueEdges.length, 1);
assert.ok(
  continueEdges.every((edge) => edge.dataset.edgeReturn === 'true'),
  'the logical loop has one return edge',
);
assert.equal(loopContainer.querySelectorAll('[data-edge-route="break"]').length, 1);
assert.equal(loopContainer.querySelectorAll('[data-edge-route="exhausted"]').length, 1);
assert.notEqual(loopContainer.querySelector('[data-edge-route="break"] path').getAttribute('d'),
  loopContainer.querySelector('[data-edge-route="exhausted"] path').getAttribute('d'), 'exit branches remain distinguishable');
await act(async () => loopContainer.querySelector('[data-node-id="historical-work-1"]').dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true })));
assert.match(loopContainer.textContent, /节点 ID：work/);
assert.doesNotMatch(loopContainer.textContent, /第 1\/20/);

let loopSerial = 10;
async function showLoop(value) {
  globalThis.fetch = async () => Response.json(value);
  await act(async () => {
    loopRoot.render(React.createElement(CollaborationDefinitionPreview, {
      key: ++loopSerial,
      snapshot: { ...snapshot, snapshotId: `44444444-4444-4444-8444-${String(loopSerial).padStart(12, '0')}` },
    }));
    await new Promise(resolve => setTimeout(resolve, 30));
  });
}
// Historical responses without descriptors still use their execution projection.
const legacyLoop = structuredClone(longLoopPreview);
delete legacyLoop.graph.loops;
await showLoop(legacyLoop);
assert.equal(loopContainer.querySelectorAll('[data-node-id]').length, 21);
assert.ok(nodeY('historical-work-19') < nodeY('historical-work-20'));
assert.equal(loopContainer.querySelectorAll('[data-edge-route="continue"][data-edge-return="false"]').length, 19);

const single = structuredClone(longLoopPreview);
single.graph.loops.rounds.max_iterations = 1;
single.graph.nodes = [single.graph.nodes[0], publishNode];
single.graph.nodes[0].execution.max_iterations = 1;
single.graph.edges = [{ source: 'historical-work-1', target: 'publish', outcome: 'again',
  loop_route: { kind: 'exhausted', logical_outcome: 'exhausted' } }];
await showLoop(single);
assert.equal(loopContainer.querySelectorAll('[data-edge-route="continue"]').length, 0, 'one iteration has no return edge');

// Repeated local IDs must remain distinct across ancestor paths and iterations.
const nested = structuredClone(fixedLoopPreview);
nested.graph.nodes = [publishNode];
nested.graph.edges = [];
nested.graph.loops = {
  outer: { display_name: 'Outer', max_iterations: 2, entry_node_id: 'start', result_node_id: 'end',
    body_node_ids: ['start', 'outer', 'end'], continue_outcomes: ['again'], break_outcomes: [], exhausted_outcome: 'exhausted' },
  'outer/outer': { display_name: 'Inner', max_iterations: 2, entry_node_id: 'work', result_node_id: 'work',
    body_node_ids: ['work'], continue_outcomes: ['again'], break_outcomes: [], exhausted_outcome: 'exhausted' },
};
for (let outer = 1; outer <= 2; outer++) {
  const frame = { loop_id: 'outer', iteration: outer, max_iterations: 2 };
  for (const id of ['start', 'end']) nested.graph.nodes.push({ ...structuredClone(loopNode), node_id: `${id}-${outer}`,
    execution: { ...frame, definition_node_id: id, loop_path: [frame] } });
  for (let inner = 1; inner <= 2; inner++) {
    const child = { loop_id: 'outer', iteration: inner, max_iterations: 2 };
    nested.graph.nodes.push({ ...structuredClone(loopNode), node_id: `work-${outer}-${inner}`,
      execution: { ...child, definition_node_id: 'work', loop_path: [frame, child] } });
  }
  nested.graph.edges.push(
    { source: `start-${outer}`, target: `work-${outer}-1`, outcome: 'complete' },
    { source: `work-${outer}-1`, target: `work-${outer}-2`, outcome: 'again', loop_route: { kind: 'continue', logical_outcome: 'again' } },
    { source: `work-${outer}-2`, target: `end-${outer}`, outcome: 'again', loop_route: { kind: 'exhausted', logical_outcome: 'exhausted' } },
    { source: `end-${outer}`, target: outer === 1 ? 'start-2' : 'publish', outcome: 'again',
      loop_route: { kind: outer === 1 ? 'continue' : 'exhausted', logical_outcome: outer === 1 ? 'again' : 'exhausted' } },
  );
}
const unchanged = JSON.stringify(nested);
assert.deepEqual(decodeCollaborationDefinitionValidationResult(nested).graph.nodes.at(-1).execution.loop_path,
  nested.graph.nodes.at(-1).execution.loop_path);
await showLoop(nested);
assert.equal(JSON.stringify(nested), unchanged);
assert.equal(loopContainer.querySelectorAll('[data-node-id]').length, 4);
assert.equal(loopContainer.querySelectorAll('[data-loop-container]').length, 2);
assert.equal(loopContainer.querySelectorAll('[data-edge-route="continue"]').length, 2);
const bounds = selector => {
  const rect = loopContainer.querySelector(`${selector} rect`);
  return Object.fromEntries(['x', 'y', 'width', 'height'].map(key => [key, Number(rect.getAttribute(key))]));
};
const outerBounds = bounds('[data-loop-container="outer"]');
const innerBounds = bounds('[data-loop-container="outer/outer"]');
assert.ok(innerBounds.x > outerBounds.x && innerBounds.y > outerBounds.y);
assert.ok(innerBounds.x + innerBounds.width < outerBounds.x + outerBounds.width);
assert.ok(innerBounds.y + innerBounds.height < outerBounds.y + outerBounds.height);
assert.ok(nodeY('start-1') < innerBounds.y && nodeY('end-1') > innerBounds.y + innerBounds.height);
assert.ok(nodeY('publish') > outerBounds.y + outerBounds.height);

const malformed = structuredClone(nested);
malformed.graph.nodes.at(-1).execution.loop_path = [];
assert.throws(() => decodeCollaborationDefinitionValidationResult(malformed), /loop_path/);
const incomplete = structuredClone(single);
incomplete.graph.loops.rounds.body_node_ids.push('missing');
await showLoop(incomplete);
assert.match(loopContainer.querySelector('[role="alert"]').textContent, /缺少逻辑节点 missing/);
await act(async () => loopRoot.unmount());

dom.window.close();
console.log('Collaboration definition card, contract, and preview tests passed.');
