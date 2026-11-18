import type {
  DashboardDailyTrend,
  DashboardEvolution,
  DashboardFailureHotspots,
  DashboardOverview,
  DashboardTimeRange,
  DashboardWorkflowHealth,
  DashboardWorkflowReleases,
} from '@/domain/taskEscortWorkspace/dashboardTypes';

function invalid(): never {
  throw new Error('大盘接口返回了非预期的数据结构，请重试');
}
function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}
function record(value: unknown): Record<string, unknown> {
  return isRecord(value) ? value : invalid();
}
/** Do not coerce strings, negatives, NaN, Infinity or out-of-domain values into metrics. */
function metric(value: unknown, max = Infinity): number | null {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= max ? value : null;
}
function count(value: unknown): number | null {
  const result = metric(value);
  return result !== null && Number.isSafeInteger(result) ? result : null;
}
function text(value: unknown): string | null {
  return typeof value === 'string' ? value : null;
}
function identifier(value: unknown): string {
  return typeof value === 'string' && value.trim() ? value.trim() : invalid();
}
function boolean(value: unknown): boolean | null {
  return typeof value === 'boolean' ? value : null;
}
function rows(data: Record<string, unknown>, key: string): Record<string, unknown>[] {
  const values = data[key];
  return Array.isArray(values) ? values.map(record) : invalid();
}
function range(data: Record<string, unknown>): DashboardTimeRange {
  const from = count(data.from),
    to = count(data.to);
  if (from === null || to === null || from > to) return invalid();
  return { from, to };
}
function available(data: Record<string, unknown>, optional = false): boolean {
  if (optional && data.available === undefined) return true;
  return typeof data.available === 'boolean' ? data.available : invalid();
}
function day(value: unknown): string {
  const date = identifier(value);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) return invalid();
  const parsed = new Date(`${date}T00:00:00Z`);
  return Number.isFinite(parsed.getTime()) && parsed.toISOString().slice(0, 10) === date ? date : invalid();
}
function evolution(value: unknown): DashboardEvolution | null {
  if (!isRecord(value) || typeof value.available !== 'boolean') return null;
  return {
    available: value.available,
    verificationAvailable: boolean(value.verificationAvailable) ?? undefined,
    diagnosisCount: count(value.diagnosisCount),
    diagnosedRunCount: count(value.diagnosedRunCount),
    issueClusterCount: count(value.issueClusterCount),
    suggestionCount: count(value.suggestionCount),
    applicationAttemptCount: count(value.applicationAttemptCount),
    applicationSucceededCount: count(value.applicationSucceededCount),
    applicationFailedCount: count(value.applicationFailedCount),
    applicationSuccessRate: metric(value.applicationSuccessRate, 1),
    appliedUnverifiedCount: count(value.appliedUnverifiedCount),
    recurrenceDetectedCount: count(value.recurrenceDetectedCount),
    verifiedCount: count(value.verifiedCount),
  };
}
export function mapDashboardOverview(value: unknown): DashboardOverview {
  const data = record(value),
    totalRuns = count(data.totalRuns),
    distribution = record(data.statusDistribution);
  if (totalRuns === null) return invalid();
  return {
    totalRuns,
    workflowCount: count(data.workflowCount),
    succeededCount: count(data.succeededCount),
    failedCount: count(data.failedCount),
    runningCount: count(data.runningCount),
    terminalCount: count(data.terminalCount),
    nonTerminalCount: count(data.nonTerminalCount),
    successRate: metric(data.successRate, 1),
    prevSuccessRate: metric(data.prevSuccessRate, 1),
    avgDurationMs: metric(data.avgDurationMs),
    totalTokenUsage: count(data.totalTokenUsage),
    statusDistribution: Object.fromEntries(Object.entries(distribution).map(([key, value]) => [key, count(value)])),
    machineDurationP50: metric(data.machineDurationP50),
    prevMachineDurationP50: metric(data.prevMachineDurationP50),
    evolution: evolution(data.evolution),
  };
}
export function mapDashboardDailyTrend(value: unknown): DashboardDailyTrend {
  const data = record(value);
  return {
    ...range(data),
    dates: rows(data, 'dates').map((row) => ({
      date: day(row.date),
      runCount: count(row.runCount),
      succeededCount: count(row.succeededCount),
      failedCount: count(row.failedCount),
      successRate: metric(row.successRate, 1),
      avgDurationMs: metric(row.avgDurationMs),
      tokenUsage: count(row.tokenUsage),
    })),
  };
}
export function mapDashboardFailureHotspots(value: unknown): DashboardFailureHotspots {
  const data = record(value);
  return {
    ...range(data),
    hotspots: rows(data, 'hotspots').map((row) => ({
      nodeLabel: identifier(row.nodeLabel),
      workflowTitle: identifier(row.workflowTitle),
      sceneName: text(row.sceneName),
      count: count(row.count),
      sharePct: metric(row.sharePct, 100),
      exampleError: text(row.exampleError),
      ticketRef: text(row.ticketRef),
    })),
  };
}
export function mapDashboardWorkflowHealth(value: unknown): DashboardWorkflowHealth {
  const data = record(value);
  return {
    ...range(data),
    available: available(data, true),
    workflows: rows(data, 'workflows').map((row) => {
      const workflowId = identifier(row.workflowId);
      return {
        workflowId,
        workflowTitle: text(row.workflowTitle) || workflowId,
        sceneName: text(row.sceneName),
        released: boolean(row.released),
        runCount: count(row.runCount),
        failedCount: count(row.failedCount),
        completionSuccessRate: metric(row.completionSuccessRate, 1),
        selfHealTriggeredRuns: count(row.selfHealTriggeredRuns),
        selfHealSuccessRate: metric(row.selfHealSuccessRate, 1),
        machineDurationP50: metric(row.machineDurationP50),
        avgDurationMs: metric(row.avgDurationMs),
      };
    }),
  };
}
export function mapDashboardWorkflowReleases(value: unknown): DashboardWorkflowReleases {
  const data = record(value);
  return {
    available: available(data),
    workflows: rows(data, 'workflows').map((row) => {
      const workflowId = identifier(row.workflowId);
      return {
        workflowId,
        workflowTitle: text(row.workflowTitle) || workflowId,
        createdAt: count(row.createdAt),
        released: boolean(row.released),
        deployCount: count(row.deployCount),
        rollbackCount: count(row.rollbackCount),
        devCycleMs: metric(row.devCycleMs),
        lastDeployAt: count(row.lastDeployAt),
        firstRunSuccessRate: metric(row.firstRunSuccessRate, 1),
      };
    }),
  };
}
