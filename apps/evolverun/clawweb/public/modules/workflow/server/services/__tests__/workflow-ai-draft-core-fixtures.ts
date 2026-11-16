import { randomUUID } from 'node:crypto';
import { vi } from 'vitest';
import type { WorkflowAiDraftExecutionPort, WorkflowAiDraftResult } from '../../contracts/workflow-ai-draft.js';
import { createWorkflowAiDraftReceipts } from '../workflow-ai-draft-receipts.js';
import { createWorkflowAiDraftService } from '../workflow-ai-draft-service.js';
export const receiptSecret = 'synthetic-workflow-receipt-secret';
export const targetBotId = 'auto-heal-bot:bot-owner';
export const overlayYaml = 'id: flow\nversion: 7\ntitle: Unsaved editor source\nnodes:\n  - id: n\n    executor: {type: agent}\n';
export const draftRequest = () => ({ requestId: randomUUID(), mode: 'modify' as const, prompt: 'Improve the workflow', currentYaml: overlayYaml });
/** No sqlite/shared DB import: core recovery must work in a fresh process. */
export function coreFixture() {
  let clock = 1_000_000, allowed = true, result: WorkflowAiDraftResult | null = null;
  const execution: WorkflowAiDraftExecutionPort = {
    resolveTarget: vi.fn(async () => ({ botId: targetBotId })),
    dispatch: vi.fn(async request => ({ jobId: request.envelope.taskId })),
    poll: vi.fn(async () => result ? { status: 'completed' as const, result } : { status: 'running' as const }),
  };
  const options = { execution, receipts: createWorkflowAiDraftReceipts(receiptSecret), now: () => clock,
    canEdit: vi.fn(async (_workflowId: string, _actorId: string) => allowed) };
  const service = createWorkflowAiDraftService(options);
  return { options, service, execution, setResult: (value: typeof result) => { result = value; },
    advance: (ms: number) => { clock += ms; }, revoke: () => { allowed = false; },
    async prepare(request = draftRequest()) {
      const prepared = await service.prepare('owner', 'flow', request);
      return { request: { ...request, receipt: prepared.receipt }, prepared,
        context: { receipt: prepared.receipt, currentYaml: request.currentYaml } };
    },
  };
}
export function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(yes => { resolve = yes; });
  return { promise, resolve };
}
