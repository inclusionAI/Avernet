import { describe, expect, it, vi } from 'vitest';

const calls = vi.hoisted(() => ({ prepare: vi.fn(), settings: vi.fn(), push: vi.fn() }));
vi.mock('../../config/auto-heal-git-config.js', () => ({ resolveAutoHealGitSettings: calls.settings }));
vi.mock('../pack-candidate-save.js', async original => ({ ...await original<typeof import('../pack-candidate-save.js')>(), withPreparedPackCandidate: calls.prepare }));
import { PackWorkflowContentChangedError } from '../pack-candidate-save.js';
import { savePackVersionToRemote, readPackOperationFromRemote, reconcilePackOperationFromRemote } from '../pack-version-remote.js';

const sha = 'a'.repeat(40), saved = 'b'.repeat(40);
const db = { query: vi.fn(async (sql: string) => sql.includes('clawweb_pack_operations') ? [] : sql.includes('workflow_specs') ? [{ workflow_id: 'flow', pack_id: 'pack' }] : [{ max_version: 0 }]), exec: vi.fn(), transaction: vi.fn() } as never;
const input = { db, workflowId: 'flow', actorId: 'editor', gitConfig: { remoteUrl: 'fixture', username: '', token: '', email: '' },
  gitAuthor: { name: 'Fixture', email: 'fixture@example.invalid' }, sourceVersion: 'draft' as const,
  sourceCommit: sha, expectedSavedHead: sha, requestId: 'request-001', changes: [{ path: 'scripts/run.py', operation: 'replace' as const, contentBase64: 'YQ==', mode: '100644' as const }] };

describe('Pack file save without operation table', () => {
  it('pushes prepared files only to wf, with no database journal access', async () => {
    calls.settings.mockResolvedValue({ gitConfig: input.gitConfig, gitAuthor: input.gitAuthor });
    calls.push.mockResolvedValue({ branch: 'wf/flow', commit: saved });
    calls.prepare.mockImplementation(async (_input, callback) => callback({ workflowId: 'flow', workflowFile: 'workflows/flow.yaml', packId: 'pack', branch: 'wf/flow', commit: saved }, calls.push));
    const result = await savePackVersionToRemote(input);
    expect(result).toMatchObject({ saved: true, branch: 'wf/flow', commit: saved, requestId: 'request-001' });
    expect(calls.push).toHaveBeenCalledOnce();
    expect(calls.prepare).toHaveBeenCalledWith(expect.objectContaining({ preserveWorkflowContent: true }), expect.any(Function));
    expect(vi.mocked(db.query).mock.calls.some(([sql]) => String(sql).includes('clawweb_pack_operations'))).toBe(true); expect(db.exec).not.toHaveBeenCalled();
  });
  it('fails closed for workflow YAML edits instead of silently putting them in Git', async () => {
    calls.push.mockClear();
    calls.prepare.mockRejectedValueOnce(new PackWorkflowContentChangedError());
    await expect(savePackVersionToRemote({ ...input, changes: [{ ...input.changes[0], path: 'workflows/flow.yaml' }] })).rejects.toMatchObject({ code: 'WORKFLOW_YAML_REQUIRES_DB_SAVE' });
    expect(calls.push).not.toHaveBeenCalled();
  });
  it('does not claim persisted recovery is available', async () => {
    await expect(readPackOperationFromRemote({ ...input, requestId: 'request-001' })).rejects.toMatchObject({ code: 'OPERATION_RECOVERY_UNAVAILABLE' });
    await expect(reconcilePackOperationFromRemote({ ...input, requestId: 'request-001' })).rejects.toMatchObject({ code: 'OPERATION_RECOVERY_UNAVAILABLE' });
  });
});
