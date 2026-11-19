import { DemoRange, DemoRun, TERMINAL_STATUSES } from './model';

export interface TrendBucket {
  label: string;
  count: number;
  rate: number | null;
}

const HOUR = 60 * 60 * 1000;
const DAY = 24 * HOUR;
// A logical demo clock, using UTC accessors so the display is independent of the browser timezone.
const SNAPSHOT = Date.UTC(2026, 10, 18, 14, 30);
const SNAPSHOT_DAY = Date.UTC(2026, 10, 18);
const pad = (value: number) => `${value}`.padStart(2, '0');

/** Aggregate only the supplied workflow's runs; no records are synthesized for empty buckets. */
export function getTrendBuckets(range: DemoRange, runs: readonly DemoRun[]): TrendBucket[] {
  const start = SNAPSHOT - (range === '24h' ? 24 : 168) * HOUR;
  // A rolling 7-day range touches eight calendar dates, including both partial boundary days.
  const starts =
    range === '24h'
      ? Array.from({ length: 6 }, (_, index) => start + index * 4 * HOUR)
      : Array.from({ length: 8 }, (_, index) => SNAPSHOT_DAY - (7 - index) * DAY);
  return starts.map((bucketStart, index) => {
    const end = starts[index + 1] ?? SNAPSHOT;
    const bucketRuns = runs.filter((run) => {
      const time = SNAPSHOT - run.ageHours * HOUR;
      return time >= start && time >= bucketStart && (index === starts.length - 1 ? time <= end : time < end);
    });
    const terminal = bucketRuns.filter((run) => TERMINAL_STATUSES.includes(run.status));
    const successes = terminal.filter((run) => run.status === 'success').length;
    const date = new Date(bucketStart);
    const label =
      range === '7d'
        ? `${pad(date.getUTCMonth() + 1)}/${pad(date.getUTCDate())}`
        : `${bucketStart < SNAPSHOT_DAY ? '昨日 ' : ''}${pad(date.getUTCHours())}:${pad(date.getUTCMinutes())}`;
    return {
      label,
      count: bucketRuns.length,
      rate: terminal.length ? Math.round((successes / terminal.length) * 1000) / 10 : null,
    };
  });
}
