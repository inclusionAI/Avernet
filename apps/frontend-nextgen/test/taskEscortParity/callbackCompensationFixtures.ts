import type { TaskEscortParityContext } from '@/domain/taskEscortParity/types';

export const callbackCompensationContext: TaskEscortParityContext = {
  scopeKey: 'space-a:alice',
  userId: 'alice',
  workflowId: 'wf/a',
  ready: true,
  workflow: { id: 'wf/a', title: '真实工作流', runCount: 1, lastStatus: 'succeeded', ownerId: null },
  query: {},
  navigate: jest.fn(),
  refreshWorkflows: jest.fn(),
};
export const callbackCompensationCapabilities = {
  workflowId: 'wf/a',
  environment: 'PRE',
  canPreview: true as const,
  executionEnabled: true,
  blockedReasons: [],
  configs: [{ configId: 'cfg', name: '通知接收者', receiverId: 'receiver', targetOrigin: 'https://receiver.example' }],
};
export const callbackCompensationPreview = {
  previewId: 'preview',
  workflowId: 'wf/a',
  environment: 'PRE',
  receiver: callbackCompensationCapabilities.configs[0],
  expiresAt: '2099-11-28T10:00:00Z',
  items: [
    {
      itemId: 'eligible',
      flowId: 'run-eligible',
      status: 'eligible' as const,
      reasons: ['服务端已验证幂等'],
      payload: { redacted: true },
    },
    { itemId: 'delivered', flowId: 'run-delivered', status: 'delivered' as const, reasons: ['已接收'], payload: null },
    { itemId: 'review', flowId: 'run-review', status: 'review' as const, reasons: ['幂等未确认'], payload: null },
    {
      itemId: 'ineligible',
      flowId: 'run-ineligible',
      status: 'ineligible' as const,
      reasons: ['非成功运行'],
      payload: null,
    },
  ],
  summary: { eligible: 1, delivered: 1, review: 1, ineligible: 1 },
};
export const callbackCompensationBatch = {
  batchId: 'batch/a',
  workflowId: 'wf/a',
  environment: 'PRE',
  status: 'running' as const,
  items: [{ itemId: 'eligible', flowId: 'run-eligible', state: 'queued' as const, auditPending: false }],
  counts: { queued: 1 },
};
export const callbackCompensationDeferred = <T>() => {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
};
