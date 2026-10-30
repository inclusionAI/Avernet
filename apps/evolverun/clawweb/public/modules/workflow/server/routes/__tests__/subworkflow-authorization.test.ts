// @vitest-environment node
import { afterEach, describe, expect, it, vi } from 'vitest';
import Database from 'better-sqlite3';
import express, { type Request } from 'express';
import type { Server } from 'node:http';
import { SqliteDatabase } from '@avernet/clawweb-shared/server/db';
import { BotWorkflowPermissionRepository } from '@avernet/clawweb-shared/server/repositories/bot-workflow-permission-repository';
import { createSubworkflowAuthorizer } from '../subworkflow-authorization.js';
import { createSubworkflowObservabilityRouter } from '../subworkflow-observability.js';

describe('subworkflow authorization', () => {
  const databases: Database.Database[] = [];
  afterEach(() => { for (const db of databases.splice(0)) db.close(); });

  function fixture() {
    const raw = new Database(':memory:');
    databases.push(raw);
    raw.exec(`CREATE TABLE bot_workflow_permissions (
      id INTEGER PRIMARY KEY, bot_id TEXT, bot_owner_id TEXT NOT NULL,
      workflow_id TEXT NOT NULL, env TEXT NOT NULL, can_view INTEGER NOT NULL,
      can_execute INTEGER NOT NULL, can_edit INTEGER NOT NULL,
      gmt_create INTEGER NOT NULL, gmt_modified INTEGER NOT NULL
    )`);
    const repo = new BotWorkflowPermissionRepository(new SqliteDatabase(raw));
    const request = { headers: { 'x-user-id': 'admin', 'x-admin': 'true', authorization: 'Bearer forged' } } as unknown as Request;
    const grant = (botId: string | null, owner: string, workflow: string, view: number, edit: number) => repo.upsert({
      bot_id: botId, bot_owner_id: owner, workflow_id: workflow,
      can_view: view, can_execute: 0, can_edit: edit,
    });
    return { raw, repo, request, grant };
  }

  it('denies bot-scoped edit and view grants, even when hasEditPermission is true', async () => {
    const { repo, request, grant } = fixture();
    await grant('bot-a', 'member', 'edit-wf', 0, 1);
    await grant('bot-a', 'member', 'view-wf', 1, 0);
    expect(await repo.hasEditPermission('edit-wf', 'member')).toBe(true);
    expect(await repo.resolveViewScope('edit-wf', 'member')).toEqual({ botIds: ['bot-a'] });
    const editCheck = vi.spyOn(repo, 'hasEditPermission');
    const authorize = createSubworkflowAuthorizer(async () => ({ actorId: 'member', isAdmin: false }), repo);
    expect(await authorize(request, 'edit-wf', 'view')).toBeNull();
    expect(await authorize(request, 'view-wf', 'view')).toBeNull();
    expect(editCheck).not.toHaveBeenCalled();
  });

  it('allows only all-scope viewing for the authenticated actor and workflow', async () => {
    const { repo, request, grant } = fixture();
    await grant('*', 'member', 'wf', 1, 0);
    const authorize = createSubworkflowAuthorizer(async () => ({ actorId: 'member', isAdmin: false }), repo);
    expect(await authorize(request, 'wf', 'view')).toEqual({ actorId: 'member', canEdit: false });
    expect(await authorize(request, 'other-wf', 'view')).toBeNull();
    const other = createSubworkflowAuthorizer(async () => ({ actorId: 'other', isAdmin: false }), repo);
    expect(await other(request, 'wf', 'view')).toBeNull();
    expect(await authorize(request, 'wf', 'edit')).toBeNull();
  });

  it('ignores forged identity and admin headers, and rejects missing or blank principals', async () => {
    const { repo, request, grant } = fixture();
    await grant('*', 'admin', 'wf', 1, 0);
    const scope = vi.spyOn(repo, 'resolveViewScope');
    const absent = createSubworkflowAuthorizer(async () => null, repo);
    const blank = createSubworkflowAuthorizer(async () => ({ actorId: '  ', isAdmin: true }), repo);
    const member = createSubworkflowAuthorizer(async () => ({ actorId: 'member', isAdmin: false }), repo);
    expect(await absent(request, 'wf', 'view')).toBeNull();
    expect(await blank(request, 'wf', 'view')).toBeNull();
    expect(scope).not.toHaveBeenCalled();
    expect(await member(request, 'wf', 'view')).toBeNull();
    expect(scope).toHaveBeenCalledWith('wf', 'member');
  });

  it('allows verified admins to view without querying permissions, but never permits edit', async () => {
    const { repo, request } = fixture();
    const scope = vi.spyOn(repo, 'resolveViewScope');
    const authorize = createSubworkflowAuthorizer(async () => ({ actorId: 'admin', isAdmin: true }), repo);
    expect(await authorize(request, 'wf', 'view')).toEqual({ actorId: 'admin', canEdit: false });
    expect(await authorize(request, 'wf', 'edit')).toBeNull();
    expect(scope).not.toHaveBeenCalled();
  });

  it('rechecks the repository after permission revocation', async () => {
    const { repo, request, grant } = fixture();
    await grant('*', 'member', 'wf', 1, 0);
    const authorize = createSubworkflowAuthorizer(async () => ({ actorId: 'member', isAdmin: false }), repo);
    expect(await authorize(request, 'wf', 'view')).not.toBeNull();
    expect(await repo.delete('*', 'member', 'wf')).toBe(true);
    expect(await authorize(request, 'wf', 'view')).toBeNull();
  });

  it('fails closed at the route when the real permissions query fails', async () => {
    const { raw, repo } = fixture();
    raw.exec(`CREATE TABLE flow_runs (flow_id TEXT, workflow_id TEXT, status TEXT, started_at INTEGER, completed_at INTEGER);
      CREATE TABLE workflow_repair_release_records (record_id TEXT, record_kind TEXT);
      INSERT INTO flow_runs VALUES ('parent', 'wf', 'running', 1, NULL);
      DROP TABLE bot_workflow_permissions;`);
    const authorize = createSubworkflowAuthorizer(async () => ({ actorId: 'member', isAdmin: false }), repo);
    const app = express();
    app.use('/runs', createSubworkflowObservabilityRouter(new SqliteDatabase(raw), authorize));
    const server = await new Promise<Server>(resolve => {
      const listener = app.listen(0, '127.0.0.1', () => resolve(listener));
    });
    try {
      const port = (server.address() as { port: number }).port;
      const response = await fetch(`http://127.0.0.1:${port}/runs/parent/subworkflows`, {
        headers: { 'x-user-id': 'admin', 'x-admin': 'true' },
      });
      expect(response.status).toBe(503);
      expect(await response.json()).toEqual({ error: 'Observability unavailable', code: 'UNAVAILABLE' });
    } finally {
      await new Promise<void>((resolve, reject) => server.close(error => error ? reject(error) : resolve()));
    }
  });

  it('propagates principal and repository failures for the route to fail closed', async () => {
    const { repo, request } = fixture();
    const error = new Error('permission database unavailable');
    vi.spyOn(repo, 'resolveViewScope').mockRejectedValue(error);
    const authorize = createSubworkflowAuthorizer(async () => ({ actorId: 'member', isAdmin: false }), repo);
    await expect(authorize(request, 'wf', 'view')).rejects.toBe(error);
    const identityError = new Error('identity unavailable');
    const unavailable = createSubworkflowAuthorizer(async () => { throw identityError; }, repo);
    await expect(unavailable(request, 'wf', 'view')).rejects.toBe(identityError);
  });
});
