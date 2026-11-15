import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { runInNewContext } from 'node:vm';
import React from 'react';
import ReactDOM from 'react-dom';
import { renderToStaticMarkup } from 'react-dom/server';
import { JSDOM } from 'jsdom';
const pkg = JSON.parse(await readFile(new URL('../package.json', import.meta.url)));
const dom = new JSDOM('<!doctype html><html><body></body></html>');
const context = { React, ReactDOM, console, window: dom.window, document: dom.window.document, setTimeout, clearTimeout,
  AbortController, Blob, URL, URLSearchParams, TextEncoder, TextDecoder };
runInNewContext(await readFile(new URL('../dist/index.umd.js', import.meta.url), 'utf8'), context);
const asset = context.taskPanel;
assert.equal(asset.assetVersion, pkg.version);
assert.equal(asset.hostApiVersion, 1);
for (const name of ['TaskLoopView', 'TaskArtifactsView', 'createTaskPanelController', 'parseTaskPanelDeclarations']) assert.equal(typeof asset[name], 'function', name);
for (const internal of ['TaskPanel', 'TaskPanelFetcher', 'TaskArtifactsList', 'TaskSwitcherControl', 'collectTaskArtifacts', 'mapDashboard', 'resolveNodeSessionTarget']) assert.equal(internal in asset, false, internal);
const host = { getViewer: () => ({ userId: 'viewer' }), getConversation: () => null,
  transport: { requestJson: async () => ({}) }, artifacts: {}, history: {}, navigate() {}, captureOrigin() {}, restoreOrigin() {},
  present() {}, preservePanel() {}, recordInteraction() {} };
const html = renderToStaticMarkup(React.createElement(asset.TaskLoopView, { taskId: 'umd-smoke', apiBaseUrl: '', host }));
assert.match(html, /data-task-panel-root/);
const interactions = [];
const controller = asset.createTaskPanelController({ ...host, recordInteraction: record => interactions.push(record) });
controller.nav('umd-smoke').selectDefaultNode('default');
controller.nav('umd-smoke').selectNode('node', null);
controller.nav('umd-smoke').selectRootNode('root');
controller.nav('umd-smoke').drillInto('child', null);
controller.nav('umd-smoke').backToOverview();
assert.equal(controller.nav('umd-smoke').selectedNodeId, 'root');
assert.equal(controller.nav('umd-smoke').drillStack.length, 0);
assert.equal(interactions.length, 0, 'UMD task browsing must not emit Bot interactions');
controller.dispose();
console.log('PASS: real UMD exports/version, host React singleton and silent task browsing');
dom.window.close();
