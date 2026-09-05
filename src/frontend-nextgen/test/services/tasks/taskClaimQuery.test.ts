import { listMyBots } from '@/services/backendApi/collaboration/collaborationBotController';
import { isBotTaskClaimEnabled } from '@/services/tasks/taskClaimQuery';

jest.mock('@/services/backendApi/collaboration/collaborationBotController', () => ({
  listMyBots: jest.fn(),
}));

// 注意：不 mock `@/services/backendApi/types`，使用真实 isEnvelopeSuccessAnyDialect，
// 验证 service 确实按「双方言并集」判成败（正是根因 bug 的复现点）。

const mockedListMyBots = listMyBots as unknown as jest.Mock;

const page = (items: Array<{ bot_id?: string; task_claim_mode?: boolean }>) => ({
  items,
  total: items.length,
  offset: 0,
  limit: 100,
});

describe('isBotTaskClaimEnabled —— mine 数据源 + 信封方言判定（执行前任务认领门禁）', () => {
  beforeEach(() => mockedListMyBots.mockClear());

  it('请求 mine 限 limit<=100（后端上限 100,>100 会 40000）', async () => {
    mockedListMyBots.mockResolvedValue({
      code: 20000,
      message: 'OK',
      data: page([{ bot_id: 'b1', task_claim_mode: true }]),
    });
    await isBotTaskClaimEnabled('b1');
    expect(mockedListMyBots).toHaveBeenCalledWith(expect.objectContaining({ kind: 'bot', limit: 100 }));
  });

  it('mine 返回 BCS 5 位成功码(20000)+目标 Bot 已开启 → 放行(true)【根因回归点:旧 detail 实现误判】', async () => {
    mockedListMyBots.mockResolvedValue({
      code: 20000,
      message: 'OK',
      data: page([{ bot_id: 'b1', task_claim_mode: true }]),
    });
    await expect(isBotTaskClaimEnabled('b1')).resolves.toBe(true);
  });

  it('mine 返回 BCS 5 位成功码(20000)+目标 Bot 未开启 → 阻断(false)', async () => {
    mockedListMyBots.mockResolvedValue({
      code: 20000,
      message: 'OK',
      data: page([{ bot_id: 'b1', task_claim_mode: false }]),
    });
    await expect(isBotTaskClaimEnabled('b1')).resolves.toBe(false);
  });

  it('mine 返回 Python 6 位成功码(200000)+已开启 → 放行', async () => {
    mockedListMyBots.mockResolvedValue({
      code: 200000,
      message: 'OK',
      data: page([{ bot_id: 'b1', task_claim_mode: true }]),
    });
    await expect(isBotTaskClaimEnabled('b1')).resolves.toBe(true);
  });

  it('mine 返回 Python 6 位成功码(200000)+未开启 → 阻断', async () => {
    mockedListMyBots.mockResolvedValue({
      code: 200000,
      message: 'OK',
      data: page([{ bot_id: 'b1', task_claim_mode: false }]),
    });
    await expect(isBotTaskClaimEnabled('b1')).resolves.toBe(false);
  });

  it('目标 Bot 不在 mine（非当前用户可管理 Bot）→ 认领不适用,放行(true)', async () => {
    mockedListMyBots.mockResolvedValue({
      code: 20000,
      message: 'OK',
      data: page([{ bot_id: 'other', task_claim_mode: true }]),
    });
    await expect(isBotTaskClaimEnabled('b1')).resolves.toBe(true);
  });

  it('mine 列表为空 → 放行(true)', async () => {
    mockedListMyBots.mockResolvedValue({ code: 20000, message: 'OK', data: page([]) });
    await expect(isBotTaskClaimEnabled('b1')).resolves.toBe(true);
  });

  it('mine 业务错误码(40400,2xx HTTP 回包)→ 无法判定,放行(true)避免误伤', async () => {
    mockedListMyBots.mockResolvedValue({ code: 40400, message: 'error', data: null });
    await expect(isBotTaskClaimEnabled('b1')).resolves.toBe(true);
  });

  it('mine 请求抛异常(网络/鉴权)→ 放行(true)', async () => {
    mockedListMyBots.mockRejectedValue(new Error('network'));
    await expect(isBotTaskClaimEnabled('b1')).resolves.toBe(true);
  });
});
