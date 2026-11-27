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

// Stored launch semantics are validated before entering the domain read model.
const { mapOverviewDetailedRun } = require('@/services/taskEscortParity/overviewMapper');
const detailWire = { flow_id: 'r', workflow_id: 'wf', status: 'running' };
it.each(['0', 'false', '[]', '{}', '[1,"business"]'])(
  'preserves falsy and structured original params %s',
  (params_json) => {
    expect(JSON.parse(mapOverviewDetailedRun({ ...detailWire, params_json }).launchParams.text)).toEqual(
      JSON.parse(params_json),
    );
  },
);
it('uses message then clearly labeled stored command or legacy string, never mutable params', () => {
  expect(
    mapOverviewDetailedRun({ ...detailWire, input_json: '{"message":"  用户要求  ","command":"/command"}' }).launchInput
      .text,
  ).toBe('  用户要求  ');
  expect(
    mapOverviewDetailedRun({ ...detailWire, input_json: '{"message":" ","command":"/stored"}' }).launchInput,
  ).toEqual(expect.objectContaining({ label: '已存储命令', text: '/stored' }));
  expect(mapOverviewDetailedRun({ ...detailWire, input_json: '"legacy input"' }).launchInput.text).toBe('legacy input');
  expect(
    mapOverviewDetailedRun({ ...detailWire, input_json: '{"params":{"root_flow_id":"fake","secret":"no"}}' }),
  ).toEqual(
    expect.objectContaining({
      parentRunId: null,
      rootRunId: null,
      launchParams: expect.objectContaining({ text: null }),
      launchInput: expect.objectContaining({ text: null }),
    }),
  );
});
it.each([
  '{"nested":[{"_clawmind_state":{"secret":"do-not-show"}}]}',
  '{"\\u005fclawmind_state":{"secret":"do-not-show"}}',
  '{"_clawmind_state":{"secret":"do-not-show"',
  '{"message":"malformed-secret"',
])('never echoes unsupported or malformed stored params %s', (params_json) => {
  const result = mapOverviewDetailedRun({ ...detailWire, params_json });
  expect(result.launchParams.text).toBeNull();
  expect(JSON.stringify(result)).not.toContain('do-not-show');
  expect(JSON.stringify(result)).not.toContain('malformed-secret');
});
it.each([undefined, null, '', '  ', 'null'])('keeps absent launch and runtime fields unavailable for %s', (value) => {
  const result = mapOverviewDetailedRun({
    ...detailWire,
    input_json: value,
    params_json: value,
    device_id: value === 'null' ? null : value,
    bot_type: {},
    stage: [],
    parent_flow_id: ' ',
    root_flow_id: false,
  });
  expect(result.launchInput.text).toBeNull();
  expect(result.launchParams.text).toBeNull();
  expect(result.deviceId).toBeNull();
  expect(result.botType).toBeNull();
  expect(result.stage).toBeNull();
  expect(result.parentRunId).toBeNull();
  expect(result.rootRunId).toBeNull();
});

it('keeps nested business strings/numbers/falsy values intact and masks known credentials and URL secrets', () => {
  const result = mapOverviewDetailedRun({
    ...detailWire,
    params_json: JSON.stringify({
      count: 0,
      enabled: false,
      target: '0',
      mode: 'false',
      empty: '',
      absent: null,
      nested: [{ accessKey: 'hidden-access', credentials: { nested: 'hidden-executor' }, business: 'null' }],
      callback: 'https://example.invalid/path?api_key=xxxxxxxx&mode=safe',
    }),
  });
  const params = JSON.parse(result.launchParams.text);
  expect(params).toEqual(
    expect.objectContaining({ count: 0, enabled: false, target: '0', mode: 'false', empty: '', absent: null }),
  );
  expect(params.nested[0].business).toBe('null');
  expect(JSON.stringify(result)).not.toMatch(/hidden-access|hidden-executor|xxxxxxxx/);
  expect(params.callback).toContain('mode=safe');
});

it('applies extended known-key URL masking and suppresses escaped state keys inside encoded nested objects', () => {
  const urls = mapOverviewDetailedRun({
    ...detailWire,
    params_json: JSON.stringify({
      callback: 'https://example.invalid/path?access_key=xxxxxxxx&mode=safe',
      nested: { endpoint: 'https://example.invalid/path?system_context=url-system-value' },
    }),
  });
  expect(urls.launchParams.text).not.toMatch(/xxxxxxxx|url-system-value/);
  expect(urls.launchParams.text).toContain('mode=safe');
  const encoded = JSON.stringify({
    safe: 'not-launch-evidence',
    nested: '{"\\u005fclawmind_state":{"value":"internal-envelope-value"}}',
  });
  const state = mapOverviewDetailedRun({ ...detailWire, params_json: encoded });
  expect(state.launchParams.text).toBeNull();
  expect(state.launchParams.note).toContain('未记录原始启动参数');
  expect(JSON.stringify(state)).not.toMatch(/not-launch-evidence|internal-envelope-value/);
});

it('preserves recorded text whitespace and JSON-like business strings when no redaction is needed', () => {
  const message = '  { "business": "actual", "count": 0 }\n';
  const result = mapOverviewDetailedRun({
    ...detailWire,
    input_json: JSON.stringify({ message }),
    params_json: JSON.stringify({ business: '{ "mode": "safe" }' }),
  });
  expect(result.launchInput.text).toBe(message);
  expect(JSON.parse(result.launchParams.text).business).toBe('{ "mode": "safe" }');
});
