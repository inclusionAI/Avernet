import { vi } from 'vitest';
import { stringify } from 'yaml';
import * as specGit from '../pack-spec-save.js';
import type { PreparedPackCandidate } from '../pack-candidate-save.js';

export const preparedSaveIdentity = { branch: 'wf/flow', commit: 'c'.repeat(40) };
export const preparedSaveConfig = { remoteUrl: 'fixture://prepared-pack', username: 'test', email: 'test@example.invalid' };
export const preparedSaveAuthor = { name: 'Test', email: 'test@example.invalid' };

/** DB/policy unit tests keep the real callback and transaction. Only Git object
 * preparation/transport is doubled; real bare-Git coverage lives in auto-heal-wf-save.
 * Create-only tests retain their real Git ownership validation.
 */
export function mockPreparedSpecSave(onPush: (input: specGit.PrepareWorkflowSpecInput) => void | Promise<void>) {
  const original = specGit.withPreparedWorkflowSpec;
  const push = vi.fn(async (input: specGit.PrepareWorkflowSpecInput) => {
    await onPush(input);
    return { ...preparedSaveIdentity, branch: `wf/${input.workflowId}` };
  });
  const prepare = vi.spyOn(specGit, 'withPreparedWorkflowSpec').mockImplementation(async (input, callback) => {
    if (input.createOnly) return original(input, callback);
    const candidate: PreparedPackCandidate = {
      workflowId: input.workflowId, packId: input.packId, packPath: '', workflowFile: `workflows/${input.workflowId}.yaml`,
      content: stringify(input.spec), manifestContent: stringify({ id: input.packId, workflows: [input.workflowId] }),
      branch: `wf/${input.workflowId}`, commit: preparedSaveIdentity.commit, sourceVersion: 'current',
      sourceCommit: 'b'.repeat(40), expectedSavedHead: 'b'.repeat(40), bundleBase64: '',
    };
    return callback(candidate, () => push(input));
  });
  return { push, prepare };
}
