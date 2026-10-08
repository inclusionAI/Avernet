import {
  mapBotHealthSummary,
  resolveBotHealthActionAvailability,
  toHealthCheckTarget,
} from '@/services/botHealthCheck/botHealthCheckService';
import { mapBotDto } from '@/services/botWorkshop/botMapper';

describe('botHealthCheckService', () => {
  test('only Openclaw bots expose health check action', () => {
    const openclaw = mapBotDto({ bot_id: 'b1', engine: 'openclaw', status: 'ACTIVE', owner_entity_id: 'u1' }).item;
    const teclaw = mapBotDto({ bot_id: 'b2', engine: 'teclaw', status: 'ACTIVE', owner_entity_id: 'u1' }).item;

    expect(resolveBotHealthActionAvailability(openclaw, 'u1')).toMatchObject({ visible: true, enabled: true });
    expect(resolveBotHealthActionAvailability(teclaw, 'u1')).toMatchObject({ visible: false, enabled: false });
  });

  test('requires harness entity context for Openclaw health check', () => {
    const bot = mapBotDto({ bot_id: 'b1', engine: 'openclaw', status: 'ACTIVE' }).item;
    bot.harnessContext = undefined;

    expect(resolveBotHealthActionAvailability(bot, 'u1')).toMatchObject({
      visible: true,
      enabled: false,
      disabledReason: '缺少健康检查所需的实体信息',
    });
  });

  test('requires current user id and carries it into target', () => {
    const bot = mapBotDto({ bot_id: 'b1', engine: 'openclaw', status: 'ACTIVE', owner_entity_id: 'u1' }).item;

    expect(resolveBotHealthActionAvailability(bot)).toMatchObject({
      visible: true,
      enabled: false,
      disabledReason: '缺少当前用户身份',
    });
    expect(toHealthCheckTarget(bot, ' 334018 ')).toMatchObject({ botId: 'b1', userId: '334018' });
  });

  test('maps Avernet reports and filters Open Core to configuration health only', () => {
    const summary = mapBotHealthSummary(
      {
        bot_id: 'b1',
        entity_id: 'u1',
        items: [
          {
            scan_dim: 'full:L1',
            health_score: 77,
            grade: 'good',
            status: 'completed',
            findings_summary: { pass: 2, warning: 0, critical: 1 },
            check_items: [
              {
                name: '实体绑定',
                status: 'passed',
                score: 100,
                conclusion: '通过',
              },
              {
                title: '配置完整性',
                result: 'failed',
                score: 40,
                failed_reason: 'Identity 未定制',
                bad_case: '缺少 entity_id 映射',
              },
            ],
            gmt_create: '2026-08-17T11:23:26Z',
            duration_ms: 1200,
          },
          {
            scan_dim: 'task-understanding',
            health_score: 95,
            grade: 'excellent',
            status: 'completed',
          },
        ],
      },
      {
        bot_id: 'b1',
        entity_id: 'u1',
        total: 1,
        page: 1,
        size: 20,
        items: [{ id: 9, scan_dim: 'configuration', health_score: 77, status: 'completed' }],
      },
      { dimensions: ['configuration'], showRadar: false, showLogDetails: false, showRawSnapshot: false },
    );

    expect(summary.dimensions).toHaveLength(1);
    expect(summary.dimensions[0]).toMatchObject({
      key: 'configuration',
      label: '配置健康度',
      score: 77,
      status: 'passed',
      checkedCount: 2,
      passedCount: 1,
      errorCount: 1,
    });
    expect(summary.dimensions[0].checkItems).toEqual([
      expect.objectContaining({ name: '实体绑定', status: 'passed', score: 100, conclusion: '通过' }),
      expect.objectContaining({
        name: '配置完整性',
        status: 'error',
        score: 40,
        conclusion: 'Identity 未定制',
        badCase: '缺少 entity_id 映射',
      }),
    ]);
    expect(summary.healthScore).toBe(77);
    expect(summary.raw).toBeUndefined();
  });

  test('preserves completed scan status and maps result_detail as the check conclusion', () => {
    const summary = mapBotHealthSummary(
      {
        bot_id: 'b1',
        entity_id: 'u1',
        items: [
          {
            scan_dim: 'full:L1',
            health_score: 30,
            grade: 'critical',
            status: 'completed',
            failed_reason: null,
            check_items: [
              {
                check_item: 'AGENTS.md',
                status: 'completed',
                result: 'fail',
                result_detail: '缺少明确的安全边界',
                score: 25,
              },
            ],
          },
        ],
      },
      {
        bot_id: 'b1',
        entity_id: 'u1',
        total: 0,
        page: 1,
        size: 20,
        items: [],
      },
      { dimensions: ['configuration'], showRadar: false, showLogDetails: false, showRawSnapshot: false },
    );

    expect(summary.dimensions[0]).toMatchObject({
      status: 'error',
      scanStatus: 'completed',
      failedReason: null,
      checkItems: [
        expect.objectContaining({
          name: 'AGENTS.md',
          resultDetail: '缺少明确的安全边界',
          conclusion: '缺少明确的安全边界',
        }),
      ],
    });
  });

  test('keeps canonical full dimensions separate when newer L1 and older L3 records coexist', () => {
    const summary = mapBotHealthSummary(
      {
        bot_id: 'b1',
        entity_id: 'u1',
        items: [
          {
            scan_dim: 'full:L1',
            health_score: 25,
            grade: 'critical',
            status: 'completed',
            check_items: [
              { check_item: 'AGENTS.md', status: 'completed', result: 'fail', score: 20 },
              { check_item: 'SOUL.md', status: 'completed', result: 'fail', score: 35 },
              { check_item: 'TOOLS.md', status: 'completed', result: 'fail', score: 20 },
            ],
            gmt_create: '2026-05-22T13:54:39Z',
          },
          {
            scan_dim: 'full:L3',
            health_score: 87,
            grade: 'excellent',
            status: 'completed',
            check_items: [
              { check_item: '任务规划能力', status: 'completed', result: 'pass', score: 80 },
              { check_item: '执行推进能力', status: 'completed', result: 'pass', score: 80 },
              { check_item: '任务闭环能力', status: 'completed', result: 'pass', score: 100 },
            ],
            gmt_create: '2026-05-20T00:00:00Z',
          },
        ],
      },
      {
        bot_id: 'b1',
        entity_id: 'u1',
        total: 0,
        page: 1,
        size: 20,
        items: [],
      },
      {
        dimensions: ['configuration', 'planningExecution'],
        showRadar: true,
        showLogDetails: true,
        showRawSnapshot: true,
      },
    );

    expect(summary.dimensions).toEqual([
      expect.objectContaining({
        key: 'configuration',
        scanDim: 'full:L1',
        score: 25,
        status: 'error',
        passedCount: 0,
        errorCount: 3,
        updatedAt: '2026-05-22T13:54:39Z',
      }),
      expect.objectContaining({
        key: 'planningExecution',
        scanDim: 'full:L3',
        score: 87,
        status: 'passed',
        passedCount: 3,
        errorCount: 0,
        updatedAt: '2026-05-20T00:00:00Z',
      }),
    ]);
    expect(summary.healthScore).toBe(56);
    expect(summary.overallStatus).toBe('critical');
    expect(summary.latestAt).toBe('2026-05-22T13:54:39Z');
  });

  test('keeps a missing capability dimension unknown instead of treating it as healthy', () => {
    const summary = mapBotHealthSummary(
      { bot_id: 'b1', entity_id: 'u1', items: [] },
      { bot_id: 'b1', entity_id: 'u1', total: 0, page: 1, size: 20, items: [] },
      {
        dimensions: ['configuration'],
        showRadar: false,
        showLogDetails: false,
        showRawSnapshot: false,
      },
    );

    expect(summary.dimensions[0]).toMatchObject({
      key: 'configuration',
      scanDim: 'full:L1',
      score: null,
      status: 'unknown',
    });
    expect(summary.overallStatus).toBe('unknown');
  });

  test('does not relabel an unknown backend dimension as configuration health', () => {
    const summary = mapBotHealthSummary(
      {
        bot_id: 'b1',
        entity_id: 'u1',
        items: [
          {
            scan_dim: 'legacy-unrecognized-dimension',
            health_score: 99,
            grade: 'excellent',
            status: 'completed',
            gmt_create: '2026-05-23T00:00:00Z',
          },
        ],
      },
      { bot_id: 'b1', entity_id: 'u1', total: 0, page: 1, size: 20, items: [] },
      {
        dimensions: ['configuration'],
        showRadar: false,
        showLogDetails: false,
        showRawSnapshot: false,
      },
    );

    expect(summary.dimensions[0]).toMatchObject({
      key: 'configuration',
      scanDim: 'full:L1',
      score: null,
      status: 'unknown',
    });
    expect(summary.healthScore).toBeNull();
    expect(summary.latestAt).toBeNull();
  });
});

test('desktop OpenClaw does not expose cloud health diagnosis', () => {
  const bot = mapBotDto({
    bot_id: 'desktop',
    bot_type: 'desktop',
    engine: 'openclaw',
    status: 'ACTIVE',
    owner_entity_id: 'owner',
  }).item;
  expect(resolveBotHealthActionAvailability(bot, 'owner')).toMatchObject({ visible: false, enabled: false });
  expect(toHealthCheckTarget(bot, 'owner')).toBeUndefined();
});
