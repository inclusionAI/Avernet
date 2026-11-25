import type { WatchRecord } from '../aistudio-train.js';
export function watchFixture(overrides: Partial<WatchRecord> = {}): WatchRecord {
  return { watchId: 'watch-1', flowId: 'flow-1', workflowId: 'wf-1', nodeId: 'train', attempt: 1,
    ownerBotId: 'bot:owner', mode: 'submit', recordId: 'record-1', detailUrl: 'https://example.org/train/record-1',
    phase: 'waiting', lastStatus: null, submittedAt: 100, firstPollAt: 700, nextPollAt: 700,
    timeoutAt: 3700, pollIntervalSeconds: 60, lastPolledAt: null, lastPollSource: null,
    errorCount: 0, lastError: null, leaseOwner: null, leaseExpiresAt: null, resolutionStatus: null,
    resolutionStartedAt: null, resolvedAt: null, canceled: false, manualRequestedAt: null,
    manualRequestedBy: null, lastManualRequestedAt: null, version: 1, ...overrides };
}
