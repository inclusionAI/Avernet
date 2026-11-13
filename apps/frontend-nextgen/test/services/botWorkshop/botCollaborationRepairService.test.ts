import { repairBotCollaboration } from '@/services/backendApi/bots/botCollaborationRepairController';
import type { BackendApiEnvelope } from '@/services/backendApi/types';
import { botCollaborationRepairService } from '@/services/bots/botCollaborationRepairService';
import { parseCollaborationRepairReport } from '@/services/bots/collaborationRepairReport';
import { afterEach, describe, expect, it, jest } from '@jest/globals';

jest.mock('@/services/backendApi/bots/botCollaborationRepairController');
const repairRequest = jest.mocked(repairBotCollaboration);

afterEach(() => {
  jest.resetAllMocks();
});

type ItemStatus = 'succeeded' | 'failed' | 'unknown' | 'skipped_dependency';
type Item = { component: string; status: string; error_code: string | null };

// 修复报告契约（backend #3663 收窄后）：固定三项 ownership / profile / managers；caller_config 移出修复范围。
const COMPONENTS = ['ownership', 'profile', 'managers'] as const;

const items = (statuses: readonly ItemStatus[]): Item[] =>
  COMPONENTS.map((component, index) => ({
    component,
    status: statuses[index],
    error_code: statuses[index] === 'failed' ? 'write_failed' : null,
  }));

const report = (status: string, statuses: readonly ItemStatus[]) => ({
  bot_id: 'b1',
  status,
  items: items(statuses),
});

const expectInvalid = (value: unknown) =>
  expect(() => parseCollaborationRepairReport(value)).toThrow('未能确认修复结果，请稍后重试');

describe('parseCollaborationRepairReport', () => {
  it('derives succeeded when all three components succeeded', () => {
    const parsed = parseCollaborationRepairReport(report('succeeded', ['succeeded', 'succeeded', 'succeeded']));

    expect(parsed.status).toBe('succeeded');
    expect(parsed.items).toHaveLength(3);
    expect(parsed.items[2]).toEqual({ component: 'managers', status: 'succeeded', error_code: null });
  });

  it('derives partial_failed when some components succeeded and others failed or unknown', () => {
    const parsed = parseCollaborationRepairReport(report('partial_failed', ['succeeded', 'succeeded', 'unknown']));

    expect(parsed.status).toBe('partial_failed');
    expect(parsed.items[2]).toEqual({ component: 'managers', status: 'unknown', error_code: null });
  });

  it('derives failed when no component succeeded', () => {
    expect(
      parseCollaborationRepairReport(report('failed', ['failed', 'unknown', 'skipped_dependency'])).status,
    ).toBe('failed');
  });

  it('keeps an unknown error_code from changing the item status', () => {
    const value = report('partial_failed', ['succeeded', 'succeeded', 'unknown']);
    value.items[2].error_code = 'some_future_reason';

    const parsed = parseCollaborationRepairReport(value);

    expect(parsed.items[2]).toEqual({ component: 'managers', status: 'unknown', error_code: 'some_future_reason' });
  });

  it.each([
    ['non-object report', null],
    ['missing bot_id', { status: 'succeeded', items: items(['succeeded', 'succeeded', 'succeeded']) }],
    [
      'empty bot_id',
      { bot_id: '  ', status: 'succeeded', items: items(['succeeded', 'succeeded', 'succeeded']) },
    ],
    [
      'missing an item',
      { bot_id: 'b1', status: 'succeeded', items: items(['succeeded', 'succeeded', 'succeeded']).slice(0, 2) },
    ],
    [
      'legacy four-item report with caller_config',
      {
        bot_id: 'b1',
        status: 'succeeded',
        items: [
          ...items(['succeeded', 'succeeded', 'succeeded']),
          { component: 'caller_config', status: 'succeeded', error_code: null },
        ],
      },
    ],
    [
      'legacy not_applicable status',
      {
        bot_id: 'b1',
        status: 'succeeded',
        items: [
          { component: 'ownership', status: 'succeeded', error_code: null },
          { component: 'profile', status: 'succeeded', error_code: null },
          { component: 'managers', status: 'not_applicable', error_code: null },
        ],
      },
    ],
    [
      'duplicate component',
      {
        bot_id: 'b1',
        status: 'succeeded',
        items: [
          { component: 'ownership', status: 'succeeded', error_code: null },
          { component: 'ownership', status: 'succeeded', error_code: null },
          { component: 'managers', status: 'succeeded', error_code: null },
        ],
      },
    ],
    [
      'out-of-order components',
      {
        bot_id: 'b1',
        status: 'succeeded',
        items: [
          { component: 'profile', status: 'succeeded', error_code: null },
          { component: 'ownership', status: 'succeeded', error_code: null },
          { component: 'managers', status: 'succeeded', error_code: null },
        ],
      },
    ],
    [
      'illegal item status',
      { bot_id: 'b1', status: 'succeeded', items: items(['succeeded', 'succeeded', 'ok' as never]) },
    ],
    [
      'non-string error_code',
      {
        bot_id: 'b1',
        status: 'failed',
        items: [
          { component: 'ownership', status: 'failed', error_code: 500 },
          { component: 'profile', status: 'failed', error_code: null },
          { component: 'managers', status: 'failed', error_code: null },
        ],
      },
    ],
    [
      'declared succeeded but contains a failure',
      report('succeeded', ['succeeded', 'succeeded', 'failed']),
    ],
    ['declared partial_failed but all succeeded', report('partial_failed', ['succeeded', 'succeeded', 'succeeded'])],
    ['declared failed but one succeeded', report('failed', ['succeeded', 'failed', 'failed'])],
  ])('rejects %s', (_label, value) => {
    expectInvalid(value);
  });
});

describe('botCollaborationRepairService', () => {
  it('shares one in-flight request per user and Bot', async () => {
    let resolveRequest: (value: BackendApiEnvelope<unknown>) => void = () => {};
    const request = new Promise<BackendApiEnvelope<unknown>>((resolve) => {
      resolveRequest = resolve;
    });
    repairRequest.mockReturnValue(request);

    const first = botCollaborationRepairService.repair('b1', 'u1');
    const second = botCollaborationRepairService.repair('b1', 'u1');

    expect(second).toBe(first);
    expect(repairRequest).toHaveBeenCalledTimes(1);
    expect(repairRequest).toHaveBeenCalledWith('b1', 'u1');
    expect(botCollaborationRepairService.pending('b1', 'u1')).toBe(first);

    resolveRequest({ code: 200000, data: report('succeeded', ['succeeded', 'succeeded', 'succeeded', 'succeeded']) });

    await expect(first).resolves.toMatchObject({ status: 'succeeded' });
    expect(botCollaborationRepairService.pending('b1', 'u1')).toBeUndefined();
  });

  it('does not share an in-flight request across different users or Bots', async () => {
    const resolvers: Array<(value: BackendApiEnvelope<unknown>) => void> = [];
    repairRequest.mockImplementation(
      () =>
        new Promise<BackendApiEnvelope<unknown>>((resolve) => {
          resolvers.push(resolve);
        }),
    );

    const first = botCollaborationRepairService.repair('b1', 'u1');
    const otherUser = botCollaborationRepairService.repair('b1', 'u2');
    const otherBot = botCollaborationRepairService.repair('b2', 'u1');

    expect(otherUser).not.toBe(first);
    expect(otherBot).not.toBe(first);
    expect(repairRequest).toHaveBeenCalledTimes(3);
    expect(resolvers).toHaveLength(3);

    const success: BackendApiEnvelope<unknown> = {
      code: 200000,
      data: report('succeeded', ['succeeded', 'succeeded', 'succeeded', 'succeeded']),
    };
    resolvers.forEach((resolve) => resolve(success));
    await Promise.all([first, otherUser, otherBot]);
    expect(botCollaborationRepairService.pending('b1', 'u1')).toBeUndefined();
    expect(botCollaborationRepairService.pending('b1', 'u2')).toBeUndefined();
    expect(botCollaborationRepairService.pending('b2', 'u1')).toBeUndefined();
  });

  it('allows a manual retry after failure and preserves the original transport error', async () => {
    const transportError = new Error('network down');
    repairRequest
      .mockRejectedValueOnce(transportError)
      .mockResolvedValueOnce({
        code: 200000,
        data: report('succeeded', ['succeeded', 'succeeded', 'succeeded', 'succeeded']),
      });

    await expect(botCollaborationRepairService.repair('b1', 'u1')).rejects.toBe(transportError);
    expect(botCollaborationRepairService.pending('b1', 'u1')).toBeUndefined();

    await expect(botCollaborationRepairService.repair('b1', 'u1')).resolves.toMatchObject({ status: 'succeeded' });
    expect(repairRequest).toHaveBeenCalledTimes(2);
  });

  it('rejects a non-success Backend envelope instead of trusting HTTP 200', async () => {
    repairRequest.mockResolvedValue({ code: 400000, message: 'forbidden', data: null });

    await expect(botCollaborationRepairService.repair('b1', 'u1')).rejects.toThrow('Bot 信息修复失败');
    expect(botCollaborationRepairService.pending('b1', 'u1')).toBeUndefined();
  });

  it('rejects an invalid report from a successful envelope', async () => {
    repairRequest.mockResolvedValue({ code: 200000, data: { bot_id: 'b1', status: 'succeeded', items: [] } });

    await expect(botCollaborationRepairService.repair('b1', 'u1')).rejects.toThrow(
      '未能确认修复结果，请稍后重试',
    );
  });
});
