import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { webcrypto } from 'node:crypto';
import { JSDOM } from 'jsdom';
const dom = new JSDOM('<html><body></body></html>', { url: 'http://local.test/bcn/chat/detail?id=old' });
Object.assign(globalThis, { window: dom.window, document: dom.window.document, HTMLElement: dom.window.HTMLElement,
  Node: dom.window.Node, IS_REACT_ACT_ENVIRONMENT: true });
Object.defineProperty(globalThis, 'crypto', { value: webcrypto, configurable: true });
const { default: React, act } = await import('react');
const { createRoot } = await import('react-dom/client');
const { CollaborationDefinitionPreview } = createRequire(import.meta.url)('..');
const fixture = JSON.parse(readFileSync(new URL('../../../tests/fixtures/collaboration-definition-draft/indentation-regression.json', import.meta.url)));
const validation = JSON.parse(readFileSync(new URL('./fixtures/incident-loop-preview.json', import.meta.url)));
const reference = { schemaVersion: 2, draftId: '750c52c6-60e2-4249-8ee5-1fb8cb9402ab', revision: 1, readToken: 'A'.repeat(43) };
const revision = { ...fixture.revision, draft_id: reference.draftId };
let mode = 'valid', calls = [], received = [];
globalThis.fetch = async (url, options) => {
  calls.push(url);
  assert(url.includes('/drafts/'), 'preview must not query users, Bots or create groups');
  assert.equal(options.headers['X-BCS-Draft-Read-Token'], reference.readToken);
  if (mode === 'revoked') return new Response('{}', { status: 404 });
  if (url.endsWith('/validate')) return Response.json({ draft_id: reference.draftId, revision: 1,
    content_hash: revision.content_hash, validated_at: '2026-10-22T00:00:00Z', validator: 'test', validation });
  return Response.json(mode === 'corrupt' ? { ...revision, size_bytes: 1 } : revision);
};
const container = document.createElement('div'); document.body.append(container);
let root;
const pause = async () => act(async () => { await new Promise(resolve => setTimeout(resolve, 30)); });
const launchButton = () => [...container.querySelectorAll('button')].find(b => b.textContent === '发起协作');
async function mount(props = {}) {
  if (root) await act(async () => root.unmount());
  calls = []; received = [];
  root = createRoot(container);
  await act(async () => root.render(React.createElement(CollaborationDefinitionPreview, { snapshot: reference, ...props })));
  await pause();
}
await mount({ onOpenCreateGroup: ref => received.push(ref) });
assert.equal(calls.length, 2); assert.equal(received.length, 0);
await act(async () => launchButton().click());
assert.deepEqual(received, [{ draft: reference }]);
assert.equal(Object.keys(received[0]).length, 1, 'only the initial Draft option crosses the callback');
assert.equal(calls.length, 2); assert.equal(container.querySelector('form'), null);
await act(async () => launchButton().click());
assert.deepEqual(received, [{ draft: reference }, { draft: reference }], 'host owns reopening the same existing modal');
await mount(); await act(async () => launchButton().click());
assert.match(container.textContent, /当前宿主尚未接入建群入口/);
assert.equal(container.querySelector('form'), null); assert.equal(calls.length, 2);
for (mode of ['revoked', 'corrupt']) {
  await mount({ onOpenCreateGroup: ref => received.push(ref) });
  assert.equal(launchButton().disabled, true); assert.equal(received.length, 0);
}
mode = 'valid';
await mount({ onOpenCreateGroup: () => { throw new Error('not available'); } });
await act(async () => launchButton().click());
assert.match(container.textContent, /建群浮层未能打开/);
await act(async () => root.unmount());
console.log('Collaboration launch bridge: fixed-reference callback, explicit click, read-only fallback and failed-source guards passed.');
