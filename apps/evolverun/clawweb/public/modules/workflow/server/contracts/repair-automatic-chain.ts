import { canonicalRepairJson, RepairBatchError, type RepairBatchInput, type RepairItem, type RepairRevision } from './repair-batch.js';

function stop(): never { throw new RepairBatchError('STATE_CONFLICT', '自动优化批次尚未完整校验，或冻结范围、基线、候选链已变化；请保留任务并重新核对', { reasonCode: 'AUTOMATIC_CHAIN_INCOMPLETE' }); }
export function isAutomaticBatch(input: RepairBatchInput): boolean { return input.automation?.roundId !== undefined; }
function roundIdentity(input: RepairBatchInput): string {
  const origin = input.automation!;
  const { batchIndex: _index, ...identity } = origin;
  return canonicalRepairJson(identity);
}
/** Validate every persisted link; item outcomes only refer to the batch that produced them. */
export function automaticChain(revisions: RepairRevision[], final: RepairRevision, requireFinal = false): RepairRevision[] {
  if (!isAutomaticBatch(final.input)) return [final];
  const origin = final.input.automation!;
  if (requireFinal && (origin.batchIndex !== origin.batchCount! - 1 || revisions[0]?.revision !== final.revision)) stop();
  const chain = revisions.filter(r => r.revision <= final.revision && r.phase !== 'failed').sort((a, b) => a.revision - b.revision);
  if (chain.length !== origin.batchIndex! + 1) stop();
  let parent: string | null = null;
  const items = new Set<string>();
  for (const [index, revision] of chain.entries()) {
    if (revision.input.automation?.batchIndex !== index || roundIdentity(revision.input) !== roundIdentity(final.input)
      || canonicalRepairJson(revision.input.baseline) !== canonicalRepairJson(final.input.baseline)
      || revision.input.parentCandidateCommit !== parent || !['review', 'no_change', 'publishing', 'published'].includes(revision.phase)
      || (revision.checks?.static as { status?: string })?.status !== 'passed'
      || typeof revision.draft?.candidateCommit !== 'string' || !Array.isArray(revision.draft.itemResults)
      || revision.draft.itemResults.some(outcome => outcome.status === 'unresolved')) stop();
    for (const item of revision.input.items) { if (items.has(item.itemId)) stop(); items.add(item.itemId); }
    parent = revision.draft.candidateCommit as string;
  }
  if (requireFinal && origin.totalItems !== undefined && items.size !== origin.totalItems) stop();
  return chain;
}
export function automaticAdvance(revisions: RepairRevision[], input: RepairBatchInput): void {
  const previous = revisions[0];
  if (!previous) { if (isAutomaticBatch(input) && (input.automation!.batchIndex !== 0 || input.parentCandidateCommit !== null)) stop(); return; }
  if (!isAutomaticBatch(previous.input) && !isAutomaticBatch(input)) return;
  if (!isAutomaticBatch(previous.input) || !isAutomaticBatch(input)) stop();
  if (previous.phase === 'failed') {
    const { revision: _old, previousReportRef: _ref, ...frozen } = previous.input;
    const { revision: _next, previousReportRef: _nextRef, ...retry } = input;
    if (canonicalRepairJson(frozen) !== canonicalRepairJson(retry)) stop();
    const successful = revisions.find(r => r.phase !== 'failed');
    if (successful) automaticChain(revisions, successful);
    else if (input.automation!.batchIndex !== 0) stop();
    return;
  }
  automaticChain(revisions, previous);
  if (roundIdentity(previous.input) !== roundIdentity(input) || input.automation!.batchIndex !== previous.input.automation!.batchIndex! + 1
    || input.automation!.batchIndex! >= input.automation!.batchCount!
    || input.parentCandidateCommit !== previous.draft?.candidateCommit) stop();
  const known = new Set(revisions.flatMap(r => r.input.items.map(item => item.itemId)));
  if (input.items.some(item => known.has(item.itemId))) stop();
}
export function changedChainItems(revisions: RepairRevision[], final: RepairRevision, requireFinal = false): RepairItem[] {
  return automaticChain(revisions, final, requireFinal).flatMap(revision => revision.input.items.filter(item =>
    Array.isArray(revision.draft?.itemResults) && revision.draft.itemResults.some(outcome => outcome.itemId === item.itemId && outcome.status === 'changed')));
}
