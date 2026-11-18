/** Protocol DTOs only. The service validates untrusted responses before mapping into Domain. */
import { backendRequest } from '../httpClient';

export interface DashboardRangeParams {
  from: number;
  to: number;
}
export interface DashboardEvolutionDto {
  available: boolean;
  verificationAvailable?: boolean;
  diagnosisCount: number;
  diagnosedRunCount?: number;
  issueClusterCount?: number;
  suggestionCount: number;
  applicationAttemptCount: number;
  applicationSucceededCount: number;
  applicationFailedCount: number;
  applicationSuccessRate: number | null;
  appliedUnverifiedCount: number | null;
  recurrenceDetectedCount: number | null;
  verifiedCount: number | null;
}
export interface DashboardOverviewDto {
  workflowCount: number;
  totalRuns: number;
  succeededCount: number;
  failedCount: number;
  runningCount: number;
  terminalCount: number;
  nonTerminalCount: number;
  successRate: number | null;
  prevSuccessRate: number | null;
  avgDurationMs: number | null;
  totalTokenUsage: number;
  statusDistribution: Record<string, number>;
  machineDurationP50?: number | null;
  prevMachineDurationP50?: number | null;
  evolution?: DashboardEvolutionDto;
}
export interface DashboardDailyPointDto {
  date: string;
  runCount: number;
  succeededCount: number;
  failedCount: number;
  successRate: number | null;
  avgDurationMs: number | null;
  tokenUsage: number;
}
export interface DashboardDailyTrendDto extends DashboardRangeParams {
  dates: DashboardDailyPointDto[];
}
export interface DashboardFailureHotspotDto {
  nodeLabel: string;
  workflowTitle: string;
  sceneName: string;
  count: number;
  sharePct: number;
  exampleError: string;
  ticketRef?: string;
}
export interface DashboardFailureHotspotsDto extends DashboardRangeParams {
  hotspots: DashboardFailureHotspotDto[];
}
export interface DashboardWorkflowHealthRowDto {
  workflowId: string;
  workflowTitle: string;
  sceneName: string;
  released: boolean;
  runCount: number;
  failedCount: number;
  completionSuccessRate: number | null;
  selfHealTriggeredRuns: number;
  selfHealSuccessRate: number | null;
  machineDurationP50: number | null;
  avgDurationMs: number | null;
}
export interface DashboardWorkflowHealthDto extends DashboardRangeParams {
  available?: boolean;
  workflows: DashboardWorkflowHealthRowDto[];
}
export interface DashboardWorkflowReleaseRowDto {
  workflowId: string;
  workflowTitle: string;
  createdAt: number | null;
  released: boolean;
  deployCount: number;
  rollbackCount: number;
  devCycleMs: number | null;
  lastDeployAt: number | null;
  firstRunSuccessRate: number | null;
}
/** The request is ranged but the upstream release response has no from/to envelope. */
export interface DashboardWorkflowReleasesDto {
  available: boolean;
  workflows: DashboardWorkflowReleaseRowDto[];
}
export interface DashboardControllerApi {
  overview: (range: DashboardRangeParams, signal?: AbortSignal) => Promise<DashboardOverviewDto>;
  dailyTrend: (range: DashboardRangeParams, signal?: AbortSignal) => Promise<DashboardDailyTrendDto>;
  failureHotspots: (range: DashboardRangeParams, signal?: AbortSignal) => Promise<DashboardFailureHotspotsDto>;
  workflowHealth: (range: DashboardRangeParams, signal?: AbortSignal) => Promise<DashboardWorkflowHealthDto>;
  workflowReleaseStats: (range: DashboardRangeParams, signal?: AbortSignal) => Promise<DashboardWorkflowReleasesDto>;
}

const BASE = '/openapi/v1/harnessflow/api/dashboard';
/** Global admin aggregates. Never add a workspace workflowId filter or a response mapper here. */
export const dashboardController: DashboardControllerApi = {
  overview: (range, signal) => backendRequest(`${BASE}/overview`, { method: 'GET', params: { ...range }, signal }),
  dailyTrend: (range, signal) => backendRequest(`${BASE}/daily-trend`, { method: 'GET', params: { ...range }, signal }),
  failureHotspots: (range, signal) =>
    backendRequest(`${BASE}/failure-hotspots`, { method: 'GET', params: { ...range, limit: 10 }, signal }),
  workflowHealth: (range, signal) =>
    backendRequest(`${BASE}/workflow-health`, { method: 'GET', params: { ...range }, signal }),
  workflowReleaseStats: (range, signal) =>
    backendRequest(`${BASE}/workflow-release-stats`, { method: 'GET', params: { ...range }, signal }),
};
