// @vitest-environment node
import express from 'express';
import { once } from 'node:events';
import { afterEach, expect, it, vi } from 'vitest';
import type { BotWorkflowPermissionRepository } from '@avernet/clawweb-shared/server/repositories/bot-workflow-permission-repository';
import { createWorkflowsRouter } from '../workflows.js';

let server: ReturnType<express.Application['listen']> | undefined;
afterEach(async () => {
  if (server) await new Promise<void>((resolve, reject) => server!.close(error => error ? reject(error) : resolve()));
  server = undefined;
});

it.each([
  { view: true, edit: false, execute: false, deploy: false },
  { view: true, edit: true, execute: false, deploy: true },
  { view: false, edit: false, execute: true, deploy: false },
  { view: false, edit: false, execute: false, deploy: false },
])('reports independent execute and effective deploy capabilities: %j', async flags => {
  const getViewByIdsForOwner = vi.fn(async () => ({ restrictedIds: new Set(['flow']), viewableIds: new Set(flags.view ? ['flow'] : []) }));
  const hasEditPermission = vi.fn(async () => flags.edit);
  const hasExecutePermission = vi.fn(async () => flags.execute);
  const permissions = { getViewByIdsForOwner, hasEditPermission, hasExecutePermission } as unknown as BotWorkflowPermissionRepository;
  const app = express();
  app.use('/api/workflows', createWorkflowsRouter(null, null, permissions, null, null));
  server = app.listen(0, '127.0.0.1'); await once(server, 'listening');
  const address = server.address(); if (!address || typeof address === 'string') throw new Error('Missing fixture address');
  const response = await fetch(`http://127.0.0.1:${address.port}/api/workflows/flow/access`, { headers: { 'X-User-Id': 'fixture-actor' } });
  expect(response.status).toBe(200);
  expect(await response.json()).toEqual({ workflowId: 'flow', canView: flags.view, canEdit: flags.edit, canExecute: flags.execute, canDeploy: flags.deploy });
  expect(hasEditPermission).toHaveBeenCalledWith('flow', 'fixture-actor');
  expect(hasExecutePermission).toHaveBeenCalledWith('flow', 'fixture-actor');
});

it('does not infer Editor deployment or raw execution from a legacy administrator flag', async () => {
  const permissions = { hasEditPermission: vi.fn(async () => false), hasExecutePermission: vi.fn(async () => false) } as unknown as BotWorkflowPermissionRepository;
  const app = express();
  app.use((req, _res, next) => { req.isAdmin = true; next(); });
  app.use('/api/workflows', createWorkflowsRouter(null, null, permissions, null, null));
  server = app.listen(0, '127.0.0.1'); await once(server, 'listening');
  const address = server.address(); if (!address || typeof address === 'string') throw new Error('Missing fixture address');
  const response = await fetch(`http://127.0.0.1:${address.port}/api/workflows/flow/access`, { headers: { 'X-User-Id': 'fixture-actor' } });
  expect(await response.json()).toEqual({ workflowId: 'flow', canView: true, canEdit: true, canExecute: false, canDeploy: false });
});
