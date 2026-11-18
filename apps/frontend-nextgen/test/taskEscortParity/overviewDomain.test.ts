import {
  overviewListQuery,
  overviewStatistics,
  overviewTimeRange,
  parseOverviewTimestamp,
  readOverviewFilters,
  runListParams,
} from '@/domain/taskEscortParity/overview';

describe('real overview domain', () => {
  it('uses aggregate counts rather than the one sampled record', () => {
    expect(
      overviewStatistics({
        succeeded: 80,
        failed: 10,
        aborted: 2,
        canceled: 3,
        running: 4,
        waiting: 5,
        blocked: 6,
        queued: 7,
        unknown: 8,
      }),
    ).toEqual(
      expect.objectContaining({ succeeded: 80, abnormal: 15, terminal: 95, total: 125, successRate: (80 / 95) * 100 }),
    );
    expect(overviewStatistics(null)).toBeNull();
    expect(overviewStatistics({})).toEqual(expect.objectContaining({ total: 0, successRate: null }));
  });
  it('normalizes verified seconds, numeric strings, milliseconds and ISO without 1970', () => {
    const ms = Date.parse('2026-11-17T08:00:00Z');
    for (const value of [ms / 1000, String(ms / 1000), ms, String(ms), '2026-11-17T08:00:00Z'])
      expect(parseOverviewTimestamp(value)).toBe(ms);
    for (const value of [null, undefined, '', 'bad-date', Infinity, {}])
      expect(parseOverviewTimestamp(value)).toBeNull();
  });
  it('uses second-based today/yesterday boundaries and real server paging/search filters', () => {
    const now = new Date(2026, 10, 17, 14).getTime();
    const today = Math.floor(new Date(2026, 10, 17).getTime() / 1000);
    expect(overviewTimeRange('today', now)).toEqual({ from: today, to: Math.floor(now / 1000) });
    expect(overviewTimeRange('yesterday', now)).toEqual({ from: today - 86400, to: today - 1 });
    expect(runListParams('wf', 'alice', { page: 2, status: 'cancelled', query: ' needle ', time: '7d' }, now)).toEqual(
      expect.objectContaining({
        user_id: 'alice',
        workflowId: 'wf',
        limit: 20,
        offset: 40,
        statuses: 'cancelled,canceled',
        query: 'needle',
        from: expect.any(Number),
      }),
    );
  });
});

it('reads committed runRange and retains the legacy runTime alias without overriding explicit URL values', () => {
  expect(
    readOverviewFilters({ runRange: '3d', runPage: '2', runStatus: 'failed', runQuery: 'history' }).filters,
  ).toEqual({ time: '3d', page: 2, status: 'failed', query: 'history' });
  expect(readOverviewFilters({ runTime: '1d' }).filters.time).toBe('1d');
  expect(overviewListQuery({ time: '3d', page: 2, status: 'failed', query: 'history' }, 'draft').runRange).toBe('3d');
});
