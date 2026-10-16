import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import type { WorkflowVersionComparison } from '../contracts/workflow-version-comparison.js';
import { WorkflowVersionComparisonRepository } from '../repositories/workflow-version-comparison-repository.js';
import { createWorkflowVersionSourceService } from './workflow-version-source-service.js';

export type VersionComparisonQuery = { baseline?: number; current?: number; days?: number };
export function deploymentTimestampMs(value: number | string): number {
  const numeric = Number(value);
  if (Number.isFinite(numeric)) return numeric >= 1e12 ? numeric : numeric * 1000;
  const parsed = Date.parse(String(value).replace(' ', 'T') + (/Z$|[+-]\d\d:\d\d$/.test(String(value)) ? '' : 'Z'));
  if (!Number.isFinite(parsed)) throw new Error('Invalid deployment timestamp');
  return parsed;
}
export function createWorkflowVersionComparisonService(db: IDatabase, clock = Date.now) {
  const repo = new WorkflowVersionComparisonRepository(db);
  return { async compare(workflowId: string, query: VersionComparisonQuery = {}): Promise<WorkflowVersionComparison> {
    const days = query.days ?? 7;
    if (!Number.isInteger(days) || days < 1 || days > 30
      || [query.baseline, query.current].some(n => n !== undefined && (!Number.isSafeInteger(n) || n < 1))) {
      throw new Error('INVALID_COMPARISON: deployment numbers must be positive integers; days must be 1–30');
    }
    const [rows, sources] = await Promise.all([repo.versions(workflowId), createWorkflowVersionSourceService(db).forWorkflow(workflowId)]);
    const versions = rows.map(row => ({ deployNumber: Number(row.deploy_number), version: Number(row.version), active: !!row.is_active,
      effectiveAtMs: sources.get(Number(row.deploy_number))?.effectiveAtMs ?? deploymentTimestampMs(row.gmt_create),
      source: sources.get(Number(row.deploy_number)) ?? null }));
    const current = query.current === undefined ? versions.find(v => v.active) : versions.find(v => v.deployNumber === query.current);
    const baselineNumber = query.baseline ?? current?.source?.baselineDeployNumber;
    const baseline = versions.find(v => v.deployNumber === baselineNumber);
    if ((query.current !== undefined && !current) || (query.baseline !== undefined && !baseline)) throw new Error('INVALID_COMPARISON: actual release deployment not found');
    const empty = { versions, baseline: null, current: null, differencePercentagePoints: null, excludedUnknownDeployment: 0, windowMs: 0 };
    if (!current) return { ...empty, status: 'no_active_version' };
    if (!baseline) return { ...empty, status: 'choose_baseline' };
    if (current.deployNumber === baseline.deployNumber) throw new Error('INVALID_COMPARISON: choose two distinct deployments');
    // Same elapsed time since each publication, clipped to available observation time.
    // Exact deployment identity prevents a mixed-version day from contaminating either side.
    const windowMs = Math.max(0, Math.min(days * 86400000, clock() - current.effectiveAtMs, clock() - baseline.effectiveAtMs));
    const [before, after] = await Promise.all([repo.observe(workflowId, baseline.deployNumber, baseline.effectiveAtMs, baseline.effectiveAtMs + windowMs),
      repo.observe(workflowId, current.deployNumber, current.effectiveAtMs, current.effectiveAtMs + windowMs)]);
    return { versions, baseline: before, current: after, windowMs,
      differencePercentagePoints: before.successRate === null || after.successRate === null ? null : after.successRate - before.successRate,
      excludedUnknownDeployment: await repo.unknown(workflowId, [before, after]),
      status: before.terminalRuns && after.terminalRuns ? 'ready' : 'no_samples' };
  } };
}
export type WorkflowVersionComparisonService = ReturnType<typeof createWorkflowVersionComparisonService>;
