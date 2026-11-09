// @vitest-environment node
import { expect, it, vi } from 'vitest';
import { WorkflowUnifiedSaveService } from '../workflow-unified-save-service.js';

it('rejects a non-boolean edit grant before reading or writing either store', async () => {
  const read = vi.fn(); const save = vi.fn(); const canWritePack = vi.fn();
  const service = new WorkflowUnifiedSaveService({
    draft: { read, save }, canEdit: async () => 'yes' as unknown as boolean, canWritePack,
    repositoryId: 'a'.repeat(64), config: { remoteUrl: 'unused', username: 'test', email: 'test@example.invalid' },
  });
  const result = await service.save({ requestId: 'request_123', actorId: 'editor', workflowId: 'flow', packId: 'pack',
    repositoryId: 'a'.repeat(64), sourceVersion: 'current', sourceCommit: 'b'.repeat(40), expectedSavedHead: null,
    expectedDraftToken: 'c'.repeat(64), specJson: JSON.stringify({ id: 'flow', nodes: [] }), changes: [] }, 'editor');
  expect(result).toMatchObject({ outcome: 'rejected', evidence: { db: 'not_started', git: 'not_started' } });
  expect(read).not.toHaveBeenCalled(); expect(save).not.toHaveBeenCalled(); expect(canWritePack).not.toHaveBeenCalled();
});
