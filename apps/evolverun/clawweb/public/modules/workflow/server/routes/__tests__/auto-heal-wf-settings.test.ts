// @vitest-environment node
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { Request, Response } from 'express';
import * as settings from '../../config/auto-heal-git-config.js';
import { AutoHealSaveService } from '../../services/auto-heal-save-service.js';
import { createAutoHealRouter, type AutoHealRouteDependencies } from '../auto-heal.js';

const deps = { db: {}, workflowSpecRepo: {}, deployHistoryRepo: {}, botPermRepo: {},
  resolvePrincipal: async () => ({ actorId: 'editor' }), gitConfig: { remoteUrl: '', username: '', email: '' },
  gitAuthor: { name: '', email: '' } } as unknown as AutoHealRouteDependencies;
const resolved = { gitConfig: { remoteUrl: '/tmp/git', username: 'user', email: 'user@example.invalid' },
  gitAuthor: { name: 'Editor', email: 'editor@example.invalid' } };
function post(endpoint: string, body: object) {
  return new Promise<{ status: number; body: Record<string, unknown> }>((resolve, reject) => {
    const req = { method: 'POST', url: endpoint, body, headers: {} } as Request; let status = 200;
    const res = { status(code: number) { status = code; return this; },
      json(value: Record<string, unknown>) { resolve({ status, body: value }); return this; } } as Response;
    createAutoHealRouter(deps)(req, res, error => reject(error ?? new Error('No response')));
  });
}
afterEach(() => vi.restoreAllMocks());
describe('/save Git settings', () => {
  const body = { workflowId: 'flow', spec: { id: 'flow' } };
  it('resolves per request and cannot enable DB-only from the request', async () => {
    const resolve = vi.spyOn(settings, 'resolveAutoHealGitSettings').mockResolvedValue(resolved);
    const save = vi.spyOn(AutoHealSaveService.prototype, 'save').mockResolvedValue({ ok: true, saved: true, workflowId: 'flow', packId: 'pack', branch: 'wf/flow', commit: 'a'.repeat(40) });
    const firstDraft = vi.spyOn(AutoHealSaveService.prototype, 'saveEditorFirstDraft');
    expect(await post('/save', { ...body, dbOnly: true })).toMatchObject({ status: 200, body: { branch: 'wf/flow' } });
    expect(resolve).toHaveBeenCalledWith(deps.db, deps.gitConfig, deps.gitAuthor);
    expect(save).toHaveBeenCalledWith(expect.objectContaining({ workflowId: 'flow', actorId: 'editor' }));
    expect(firstDraft).not.toHaveBeenCalled();
  });
  it('does not invoke the service when Git settings fail', async () => {
    vi.spyOn(settings, 'resolveAutoHealGitSettings').mockRejectedValue(Object.assign(new Error('private'), { status: 503 }));
    const save = vi.spyOn(AutoHealSaveService.prototype, 'save');
    expect(await post('/save', body)).toMatchObject({ status: 503, body: { code: 'SAVE_FAILED' } });
    expect(save).not.toHaveBeenCalled();
  });
});

describe('/editor-save-first-draft', () => {
  it('uses only the explicit DB-only service and does not resolve Git settings', async () => {
    const resolve = vi.spyOn(settings, 'resolveAutoHealGitSettings');
    const save = vi.spyOn(AutoHealSaveService.prototype, 'save');
    const firstDraft = vi.spyOn(AutoHealSaveService.prototype, 'saveEditorFirstDraft').mockResolvedValue({ ok: true, saved: true, workflowId: 'flow', packId: 'pack' });
    expect(await post('/editor-save-first-draft', { workflowId: 'flow', spec: { id: 'flow' }, expectedDraftAbsent: true,
      expectedActiveDeployNumber: 1, expectedActiveToken: 'a'.repeat(64) })).toMatchObject({ status: 200, body: { packId: 'pack' } });
    expect(resolve).not.toHaveBeenCalled(); expect(save).not.toHaveBeenCalled();
    expect(firstDraft).toHaveBeenCalledWith(expect.objectContaining({ workflowId: 'flow', actorId: 'editor' }));
  });
});
