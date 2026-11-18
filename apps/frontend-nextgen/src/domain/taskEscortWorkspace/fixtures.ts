import { demoExperiences, demoSettings } from './demoExperiences';
import { demoIssues } from './demoIssues';
import { periodMetrics } from './demoMetrics';
import { demoRuns } from './demoRuns';
import { demoWorkflows } from './demoWorkflows';
import { immutableCopy } from './immutable';
import type { TaskEscortWorkspaceSnapshot } from './types';

/** Authored synthetic review snapshot. Never used as a live failure fallback. */
export const demoFixtures = immutableCopy({
  workflows: demoWorkflows,
  runs: demoRuns,
  issues: demoIssues,
  experiences: demoExperiences,
  settings: demoSettings,
});
export const demoPeriodMetrics = immutableCopy(periodMetrics);
export function freshDemoSnapshot(): TaskEscortWorkspaceSnapshot {
  return immutableCopy({ ...demoFixtures, settingsDirty: false, loading: false, error: null });
}
