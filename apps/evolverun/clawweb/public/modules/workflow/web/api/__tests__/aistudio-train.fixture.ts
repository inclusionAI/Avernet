import type { AIStudioTrainWatch } from '../aistudio-train'

export const trainWatch: AIStudioTrainWatch = {
  watchId: 'watch-1', version: 1, recordId: 'record-42', detailUrl: 'https://example.com/training/42',
  flowId: 'flow-1', nodeId: 'train', attempt: 2, mode: 'submit', phase: 'waiting', lastStatus: 'RUNNING',
  submittedAt: '2026-11-16T00:00:00Z', firstPollAt: '2026-11-16T00:05:00Z',
  nextPollAt: '2026-11-16T00:10:00Z', timeoutAt: '2026-11-17T00:00:00Z', pollInterval: 300,
  lastPolledAt: '2026-11-16T00:05:00Z', lastPollSource: 'auto', errorCount: 0, lastError: null,
  manualPollAllowed: true, manualPollPending: false, manualPollRetryAt: null,
}
