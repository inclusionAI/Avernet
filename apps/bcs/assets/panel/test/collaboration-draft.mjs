import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { JSDOM } from 'jsdom';
import { webcrypto } from 'node:crypto';

const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://local.test/bcn/chat/detail?id=group' });
Object.assign(globalThis, {
  window: dom.window, document: dom.window.document, HTMLElement: dom.window.HTMLElement,
  Node: dom.window.Node, IS_REACT_ACT_ENVIRONMENT: true,
});
Object.defineProperty(globalThis, 'crypto', { value: webcrypto, configurable: true });
const { default: React, act } = await import('react');
const { createRoot } = await import('react-dom/client');
const { CollaborationDefinitionCard, CollaborationDefinitionPreview, decodeCollaborationDefinitionReference } = createRequire(import.meta.url)('..');
const fixture = JSON.parse(readFileSync(new URL('../../../tests/fixtures/collaboration-definition-draft/indentation-regression.json', import.meta.url), 'utf8'));
// Validator response for the unchanged 2,174-byte incident YAML above.
const incidentValidation = JSON.parse(readFileSync(new URL('./fixtures/incident-loop-preview.json', import.meta.url), 'utf8'));
const reference = { schemaVersion: 2, draftId: '750c52c6-60e2-4249-8ee5-1fb8cb9402ab', revision: 1, readToken: 'A'.repeat(43) };
assert.deepEqual(decodeCollaborationDefinitionReference(reference), reference);
for (const bad of [
  { ...reference, readToken: 'A'.repeat(42) + 'B' },
  { ...reference, readToken: undefined },
  { ...reference, revision: Number.MAX_SAFE_INTEGER + 1 },
  { ...reference, revision: 0 },
  { ...reference, title: 'forbidden' },
  { ...reference, url: 'https://untrusted.example' },
  { ...reference, apiBaseUrl: 'https://untrusted.example' },
  { ...reference, definitionYaml: 'inline' },
]) assert.throws(() => decodeCollaborationDefinitionReference(bad));
assert.equal(decodeCollaborationDefinitionReference({ ...reference, revision: Number.MAX_SAFE_INTEGER }).revision, Number.MAX_SAFE_INTEGER);

const opened = [], closed = [], requests = [];
window.aixGetLibraryCdn = () => '/bcnproxy/assets/panel/index.umd.js';
window.aixBridge = { openPanelTab: tab => opened.push(tab), closePanelTab: id => closed.push(id), focusPanelTab() {} };
globalThis.fetch = async () => { throw new Error('must load only on preview mount'); };
const container = document.createElement('div');
container.dataset.messageId = 'stored-message';
document.body.append(container);
const root = createRoot(container);
await act(async () => root.render(React.createElement(CollaborationDefinitionCard, reference)));
assert.equal(opened.length, 0);
await act(async () => container.querySelector('button').click());
assert.equal(opened.length, 1);
assert(!('apiBaseUrl' in opened[0].params.data), 'an unconfigured card must allow the preview host to inject its base');
assert(!opened[0].id.includes(reference.readToken));
await act(async () => container.querySelector('button').click());
assert.equal(opened[0].id, opened[1].id);
await act(async () => root.render(React.createElement(CollaborationDefinitionCard, { ...reference, readToken: 'B'.repeat(42) + 'A' })));
assert.equal(closed.length, 1, 'credential changes close stale preview');
await act(async () => container.querySelector('button').click());
assert.equal(opened.at(-1).id, opened[0].id, 'capability never forms part of tab identity');
await act(async () => root.unmount());

const revision = { ...fixture.revision, draft_id: reference.draftId, revision: 1 };
const validation = {
  draft_id: reference.draftId, revision: 1, content_hash: revision.content_hash,
  validated_at: '2026-10-20T02:00:00Z', validator: 'test-validator',
  validation: incidentValidation,
};
let mode = 'valid';
globalThis.fetch = async (url, options) => {
  requests.push({ url, options });
  assert.equal(options.headers['X-BCS-Draft-Read-Token'], reference.readToken);
  assert.equal(options.cache, 'no-store');
  assert.equal(options.redirect, 'error');
  assert(!url.includes(reference.readToken));
  if (mode === 'revoked') return new Response('{}', { status: 404 });
  if (url.endsWith('/validate')) {
    assert.equal(options.method, 'POST');
    assert.equal(options.body, undefined);
    if (mode === 'validation-down') return new Response('{}', { status: 503 });
    return Response.json(validation);
  }
  assert.equal(options.method, 'GET');
  if (mode === 'invalid-unicode') return Response.json({ ...revision, definition_yaml: '\ud800' });
  return Response.json(mode === 'corrupt' ? { ...revision, content_hash: '0'.repeat(64) } : revision);
};
const panel = document.createElement('div');
document.body.append(panel);
const panelRoot = createRoot(panel);
const tab = name => [...panel.querySelectorAll('[role="tab"]')].find(button => button.textContent === name);
const copyButton = () => panel.querySelector('button[aria-label="复制源码"]');
async function selectTab(name) {
  await act(async () => tab(name).click());
  assert.equal(tab(name).getAttribute('aria-selected'), 'true');
  assert.equal(document.getElementById(tab(name).getAttribute('aria-controls')).hidden, false);
}
let serial = 0;
async function render() {
  await act(async () => {
    panelRoot.render(React.createElement(CollaborationDefinitionPreview, { key: ++serial, snapshot: reference, sourceMessageId: 'stored-message' }));
    await new Promise(resolve => setTimeout(resolve, 20));
  });
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 30)); });
}
await render();
assert.equal(tab('预览').getAttribute('aria-selected'), 'true');
assert.equal(panel.querySelector('pre'), null, 'opening a draft shows preview without the source block');
assert.match(panel.textContent, /定义校验通过/);
assert.equal(panel.querySelectorAll('[data-node-id]').length, 3, 'incident shows writing, review and publish once');
assert.equal(panel.querySelectorAll('[data-loop-container]').length, 1);
assert.match(panel.querySelector('[data-loop-container="review_loop"]').textContent, /写稿审稿循环 · 3 轮/);
assert.equal(panel.querySelectorAll('[data-edge-route="continue"][data-edge-return="true"]').length, 1);
assert.equal(panel.querySelectorAll('[data-edge-route="exhausted"]').length, 1);
assert.equal(panel.querySelectorAll('[data-edge-route="break"]').length, 0, 'fixed loop must not invent early exit');
const logicalNodes = [...panel.querySelectorAll('[data-node-id]')].map(node => node.getAttribute('aria-label'));
assert.deepEqual(logicalNodes, ['节点 写稿', '节点 审稿', '节点 发表']);
let copied;
Object.defineProperty(globalThis, 'navigator', { value: { clipboard: { writeText: async text => { copied = text; } } }, configurable: true });
await act(async () => copyButton().click());
assert.equal(copied, fixture.revision.definition_yaml);
await selectTab('源码');
assert.equal(panel.querySelector('pre').textContent, fixture.revision.definition_yaml);
assert.equal(new TextEncoder().encode(panel.querySelector('pre').textContent).length, 2174);
assert.match(panel.textContent, /test-validator/);
copied = undefined;
await act(async () => copyButton().click());
assert.equal(copied, fixture.revision.definition_yaml, 'copy works in both tabs');
await act(async () => tab('源码').dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: 'ArrowLeft', bubbles: true })));
assert.equal(document.activeElement, tab('预览'));
assert.equal(tab('预览').getAttribute('aria-selected'), 'true');
assert.equal(panel.querySelector('pre'), null);
assert.equal(panel.querySelectorAll('[data-node-id]').length, 3, 'returning from source retains the logical loop view');
assert.equal(requests.length, 2, 'switching tabs and copying reuse the fixed revision without refetching');
mode = 'validation-down';
await render();
assert.equal(copyButton().disabled, false, 'verified source remains available when validation fails');
await selectTab('源码');
assert.equal(panel.querySelector('pre').textContent, fixture.revision.definition_yaml);
assert.match(panel.textContent, /HTTP 503/);
mode = 'corrupt';
await render();
assert.equal(copyButton().disabled, true);
await selectTab('源码');
assert.equal(panel.querySelector('pre'), null);
assert.match(panel.textContent, /SHA-256 不匹配/);
mode = 'invalid-unicode';
await render();
assert.equal(copyButton().disabled, true);
await selectTab('源码');
assert.equal(panel.querySelector('pre'), null);
assert.match(panel.textContent, /完整性检查失败/);
mode = 'revoked';
await render();
assert.equal(copyButton().disabled, true);
await selectTab('源码');
assert.equal(panel.querySelector('pre'), null);
assert.match(panel.textContent, /引用不可用/);
await act(async () => panelRoot.unmount());
assert(requests.every(r => r.url.includes(`/revisions/1`)));

// Exercise the real card -> UMD tab -> preview path with host configuration.
mode = 'valid';
for (const [cardProps, expectedBase, draftPath] of [
  [{ ...reference, apiBaseUrl: '/api/v1/collaboration///', baseUrl: '/ignored', data: { apiBaseUrl: '/also-ignored' } }, '/api/v1/collaboration///', '/api/v1/collaboration/drafts'],
  [{ ...reference, baseUrl: '/host-bcs/' }, '/host-bcs/', '/host-bcs/collaboration/drafts'],
  [{ ...reference, data: { apiBaseUrl: '/data-bcs', baseUrl: '/ignored' } }, '/data-bcs', '/data-bcs/collaboration/drafts'],
  [{ ...reference, data: { baseUrl: '/data-alias' } }, '/data-alias', '/data-alias/collaboration/drafts'],
  [{ params: { data: { params: reference, apiBaseUrl: '/wrapped-bcs' } } }, '/wrapped-bcs', '/wrapped-bcs/collaboration/drafts'],
  [{ payload: { data: { params: reference, baseUrl: '/payload-bcs' } } }, '/payload-bcs', '/payload-bcs/collaboration/drafts'],
]) {
  const cardRoot = createRoot(container);
  await act(async () => cardRoot.render(React.createElement(CollaborationDefinitionCard, cardProps)));
  assert.equal(container.querySelector('button').disabled, false, 'host props must not invalidate the reference');
  await act(async () => container.querySelector('button').click());
  const payload = opened.at(-1).params;
  assert.equal(payload.data.apiBaseUrl, expectedBase);
  assert.deepEqual(payload.data.snapshot, reference, 'host base must stay outside the fixed reference');
  for (const envelope of ['params', 'payload']) {
    const start = requests.length;
    const previewRoot = createRoot(panel);
    await act(async () => previewRoot.render(React.createElement(CollaborationDefinitionPreview, { [envelope]: payload })));
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 30)); });
    assert.match(panel.textContent, /定义校验通过/);
    assert.deepEqual(requests.slice(start).map(r => r.url), [
      `${draftPath}/${reference.draftId}/revisions/1`,
      `${draftPath}/${reference.draftId}/revisions/1/validate`,
    ]);
    await act(async () => previewRoot.unmount());
  }
  await act(async () => cardRoot.unmount());
}

// A transport can deliver a response after abort. Switching version/source must
// still keep it from replacing the new preview or triggering old validation.
const pending = [];
globalThis.fetch = (url, options) => new Promise(resolve => pending.push({ url, options, resolve }));
const raceRoot = createRoot(panel);
await act(async () => raceRoot.render(React.createElement(CollaborationDefinitionPreview, {
  snapshot: reference, sourceMessageId: 'source-a',
})));
assert.equal(pending.length, 1);
await act(async () => raceRoot.render(React.createElement(CollaborationDefinitionPreview, {
  snapshot: { ...reference, revision: 2 }, sourceMessageId: 'source-b',
})));
assert.equal(pending.length, 2);
assert.equal(pending[0].options.signal.aborted, true);
await act(async () => {
  pending[0].resolve(Response.json(revision));
  await new Promise(resolve => setTimeout(resolve, 20));
});
assert.equal(panel.querySelector('pre'), null);
assert.equal(pending.length, 2, 'stale source must not trigger validation');
await act(async () => {
  pending[1].resolve(Response.json({ ...revision, revision: 2 }));
  await new Promise(resolve => setTimeout(resolve, 30));
});
assert.equal(pending.length, 3);
assert(pending[2].url.endsWith('/revisions/2/validate'));
await act(async () => {
  pending[2].resolve(Response.json({ ...validation, revision: 2 }));
  await new Promise(resolve => setTimeout(resolve, 20));
});
assert.match(panel.textContent, /定义校验通过/);
await selectTab('源码');
assert.match(panel.textContent, /source-b/);
assert(!panel.textContent.includes('source-a'));
await act(async () => raceRoot.render(React.createElement(CollaborationDefinitionPreview, {
  snapshot: { ...reference, revision: 3 }, sourceMessageId: 'source-c',
})));
assert.equal(pending.length, 4);
assert.equal(tab('预览').getAttribute('aria-selected'), 'true', 'a different version opens in preview');
assert.equal(panel.querySelector('pre'), null);
assert.equal(copyButton().disabled, true, 'never copy the previous version while the new one loads');
await act(async () => raceRoot.unmount());
assert.equal(pending[3].options.signal.aborted, true);

// A host-base change alone must cancel old requests and discard old source.
const baseRoot = createRoot(panel);
const start = pending.length;
await act(async () => baseRoot.render(React.createElement(CollaborationDefinitionPreview, {
  snapshot: reference, apiBaseUrl: '/old-host',
})));
await act(async () => baseRoot.render(React.createElement(CollaborationDefinitionPreview, {
  snapshot: reference, apiBaseUrl: '/api/v1/collaboration/',
  baseUrl: '/ignored', data: { apiBaseUrl: '/also-ignored' },
})));
assert.equal(pending[start].options.signal.aborted, true);
assert.equal(pending[start + 1].url, `/api/v1/collaboration/drafts/${reference.draftId}/revisions/1`);
await act(async () => {
  pending[start].resolve(Response.json(revision));
  await new Promise(resolve => setTimeout(resolve, 20));
});
assert.equal(pending.length, start + 2, 'stale host response must not trigger validation');
assert.equal(copyButton().disabled, true);
await act(async () => {
  pending[start + 1].resolve(Response.json(revision));
  await new Promise(resolve => setTimeout(resolve, 30));
});
assert.equal(pending[start + 2].url, `/api/v1/collaboration/drafts/${reference.draftId}/revisions/1/validate`);
await act(async () => {
  pending[start + 2].resolve(Response.json(validation));
  await new Promise(resolve => setTimeout(resolve, 20));
});
assert.match(panel.textContent, /定义校验通过/);
await act(async () => baseRoot.unmount());
console.log('Draft Panel: strict references, capability isolation, preview/source tabs, incident bytes/copy, fixed validation, unavailable and integrity states passed.');
