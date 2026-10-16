import type { WorkflowVersionSource } from './workflow-version-source.js';

export type ComparisonVersion = {
  deployNumber: number;
  version: number;
  active: boolean;
  effectiveAtMs: number;
  source: WorkflowVersionSource | null;
};
export type VersionObservation = {
  deployNumber: number;
  fromMs: number;
  toMs: number;
  terminalRuns: number;
  succeededRuns: number;
  failedRuns: number;
  successRate: number | null;
  excludedUnfinished: number;
};
export type WorkflowVersionComparison = {
  versions: ComparisonVersion[];
  baseline: VersionObservation | null;
  current: VersionObservation | null;
  differencePercentagePoints: number | null;
  excludedUnknownDeployment: number;
  status: 'ready' | 'choose_baseline' | 'no_active_version' | 'no_samples';
  windowMs: number;
};
