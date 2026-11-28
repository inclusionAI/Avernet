import { describe, expect, it } from 'vitest';
import { reviewedCompensationEvidence } from '../callback-compensation-evidence.js';
import type { CompensationEvidence, EvidenceQuery } from '@avernet/workflow/server/services/callback-compensation/types';
function fixture() {
  const e: CompensationEvidence = {
    environment: 'pre', workflowId: 'wf', flowId: 'flow', receiverId: 'receiver', generation: 'independent-generation-1', retryEpoch: 0,
    snapshotHash: 'a'.repeat(64), generationEvidenceRef: 'review/generation-1', configIds: ['old-config', 'current-config'],
    urls: ['https://receiver.example.test/callback'], url: 'https://receiver.example.test/callback',
    historicalRoute: { evidenceRef: 'review/historical-routing', validFrom: '2026-11-01T00:00:00Z', validUntil: '2026-12-01T00:00:00Z' },
    legacyIdempotency: { evidenceRef: 'contract/business-deduplication', keyContract: 'workflow+flow+generation+receiver',
      legacySenderContract: 'contract/legacy-request-coverage', generationContract: 'contract/execution-generation',
      lateArrivalContract: 'contract/late-delivery-coverage', earliestCompletion: '2026-11-01T00:00:00Z', latestCompletion: '2026-11-28T00:00:00Z',
      retentionUntil: '2027-01-01T00:00:00Z', lateArrivalUntil: '2026-12-02T00:00:00Z' },
    evidenceRef: 'review/notification-recovery', validFrom: '2026-11-28T00:00:00Z', validUntil: '2026-12-01T00:00:00Z', phaseNullable: false,
  };
  const query: EvidenceQuery = { environment: 'pre', workflowId: 'wf', flowId: 'flow', configId: 'current-config', snapshotHash: e.snapshotHash,
    retryEpoch: 0, completedAt: '2026-11-28T00:00:00Z', now: '2026-11-28T08:00:00Z' };
  return { e, query, manifest: { version: 1, environment: 'pre', entries: [e] } };
}
describe('server-only reviewed callback evidence', () => {
  it('resolves only the exact environment, workflow, run, snapshot, epoch and receiver aliases', async () => {
    const { e, query, manifest } = fixture(); const p = reviewedCompensationEvidence(manifest, 'pre');
    expect(await p.resolve(query)).toEqual(e);
    expect(await p.resolve({ ...query, configId: 'old-config' })).toEqual(e);
    for (const change of [{ environment: 'prod' }, { workflowId: 'other' }, { flowId: 'other' }, { snapshotHash: 'b'.repeat(64) },
      { retryEpoch: 1 }, { configId: 'unreviewed-config' }]) expect(await p.resolve({ ...query, ...change })).toBeNull();
  });
  it('does not grant authorization from booleans, arbitrary fields or a foreign environment', () => {
    const { manifest } = fixture();
    expect(() => reviewedCompensationEvidence({ enabled: true, idempotent: true }, 'pre')).toThrow();
    expect(() => reviewedCompensationEvidence(manifest, 'prod')).toThrow();
    expect(() => reviewedCompensationEvidence({ ...manifest, secret: 'not-a-real-secret' }, 'pre')).toThrow();
    const { e } = fixture();
    expect(() => reviewedCompensationEvidence({ ...manifest, entries: [{ ...e, legacyIdempotency: { approved: true } }] }, 'pre')).toThrow();
  });
  it('cannot give one historical config or URL a new logical receiver identity', () => {
    const { e, manifest } = fixture();
    expect(() => reviewedCompensationEvidence({ ...manifest, entries: [e, { ...e, flowId: 'flow2', receiverId: 'new-receiver' }] }, 'pre')).toThrow();
  });
  it('rejects ambiguity, plaintext secret fields, insecure endpoints and insufficient retention', () => {
    const { e, manifest } = fixture();
    for (const entry of [{ ...e, secret: 'not-a-real-secret' }, { ...e, urls: ['http://receiver.example.test/callback'] },
      { ...e, legacyIdempotency: { ...e.legacyIdempotency, retentionUntil: '2026-11-01T00:00:00Z' } }]) {
      expect(() => reviewedCompensationEvidence({ ...manifest, entries: [entry] }, 'pre')).toThrow();
    }
    expect(() => reviewedCompensationEvidence({ ...manifest, entries: [e, e] }, 'pre')).toThrow();
  });
  it('copies configuration at construction and never returns a mutable authorization reference', async () => {
    const { e, query, manifest } = fixture(); const p = reviewedCompensationEvidence(manifest, 'pre');
    e.receiverId = 'tampered';
    const resolved = await p.resolve(query); resolved!.receiverId = 'tampered-again';
    expect((await p.resolve(query))?.receiverId).toBe('receiver');
  });
  it('an empty reviewed manifest never authorizes a run', async () => {
    const { query } = fixture();
    expect(await reviewedCompensationEvidence({ version: 1, environment: 'pre', entries: [] }, 'pre').resolve(query)).toBeNull();
  });
});
