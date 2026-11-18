import type { Metrics, Period } from './types';

const weekRuns = [402, 426, 448, 471, 462, 490, 455];
const weekAnomalies = [46, 45, 49, 45, 47, 43, 33];
const monthRuns = Array.from({ length: 23 }, (_, i) => 338 + ((i * 31) % 139)).concat(weekRuns);
const monthAnomalies = monthRuns
  .slice(0, 23)
  .map((count, i) => Math.round(count * (0.1 + (i % 4) * 0.005)))
  .concat(weekAnomalies);
const labels = Array.from({ length: 30 }, (_, i) =>
  new Date(Date.UTC(2026, 9, 19 + i)).toISOString().slice(5, 10).replace('-', '/'),
);
const sum = (values: number[]) => values.reduce((a, b) => a + b, 0);
const rate = (runs: number, anomalies: number) => Number((((runs - anomalies) / runs) * 100).toFixed(1));
function metrics(series: number[], failures: number[], labels: string[], p50: number, live = false): Metrics {
  const runs = sum(series);
  const anomalies = sum(failures);
  return {
    available: true,
    scope: 'demo',
    runs,
    anomalies,
    successRate: rate(runs, anomalies),
    p50,
    running: live ? 3 : 0,
    waiting: live ? 2 : 0,
    blocked: live ? 5 : 0,
    queued: live ? 8 : 0,
    series,
    successSeries: series.map((count, index) => rate(count, failures[index])),
    labels,
  };
}
export const periodMetrics: Record<Period, Metrics> = {
  today: metrics(
    [32, 25, 38, 77, 136, 147],
    [3, 2, 3, 6, 9, 10],
    ['00:00', '02:00', '04:00', '06:00', '08:00', '10:00'],
    452,
    true,
  ),
  yesterday: metrics(
    [26, 18, 15, 19, 35, 53, 64, 72, 68, 54, 39, 27],
    [3, 2, 1, 2, 4, 5, 5, 5, 6, 5, 3, 2],
    Array.from({ length: 12 }, (_, i) => `${String(i * 2).padStart(2, '0')}:00`),
    461,
  ),
  '7d': metrics(weekRuns, weekAnomalies, labels.slice(-7), 473, true),
  '30d': metrics(monthRuns, monthAnomalies, labels, 496, true),
};
