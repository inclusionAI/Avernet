import { loadRoutineContext } from '@/services/botScheduledTasks/metadata';
import { sendRoutineRequest } from '@/services/botScheduledTasks/http';
import type { RoutineStage } from '@/services/botScheduledTasks/types';

jest.mock('@/services/botScheduledTasks/http', () => ({ sendRoutineRequest: jest.fn() }));

// /containers 只报告线上容器，且描述的是 Bot 本身的线上运行属性。链接的
// runtime_stage 只是列表筛选/查看上下文（草稿/预发实例没有清单接口），不能据此
// 跳过查询——否则从草稿/预发筛选入口创建线上目标时拿不到实例清单，多实例 Bot
// 无法选择运行实例。
const sendMock = sendRoutineRequest as unknown as jest.Mock;
const botDetail = (overrides: Record<string, unknown>) => ({
  bot_id: 'b1',
  bot_name: 'Bot',
  owner_id: 'u1',
  bot_type: 'service',
  engine: 'openclaw',
  status: 'ACTIVE',
  ...overrides,
});
const mockTransport = (bot: Record<string, unknown>) =>
  sendMock.mockImplementation(async (url: string) => {
    if (url.endsWith('/caller-context')) return { bot_call_type: 'owner' };
    if (url.endsWith('/containers')) return { instances: [{ id: 'c1' }, { id: 'c2' }, {}] };
    return bot;
  });
const containerCalled = () =>
  sendMock.mock.calls.some(([url]) => typeof url === 'string' && url.includes('/containers'));
const callerContextParams = () =>
  sendMock.mock.calls
    .filter(([url]) => typeof url === 'string' && url.endsWith('/caller-context'))
    .map(([, options]) => (options as { params?: Record<string, unknown> }).params)
    .pop();
const load = (stage?: RoutineStage) =>
  loadRoutineContext({ botId: 'b1', userId: 'u1', entityId: 'u1', stage });

beforeEach(() => {
  sendMock.mockReset();
});

it.each(['draft', 'verify', undefined] as const)(
  '任何链接阶段（%s）都请求线上容器清单，填充 online 实例',
  async (stage) => {
    mockTransport(botDetail({}));
    const context = await load(stage);
    expect(containerCalled()).toBe(true);
    expect(context.environments[2].instances).toEqual(['c1', 'c2']);
    // 草稿/预发从未获得这份清单：线上 UUID 不得复用给其他环境。
    expect(context.environments[0].instances).toEqual([]);
  },
);

it('线上阶段请求线上容器并填充 online 实例清单', async () => {
  mockTransport(botDetail({}));
  const context = await load('online');
  expect(containerCalled()).toBe(true);
  expect(context.environments[2].instances).toEqual(['c1', 'c2']);
});

it('teclaw 引擎即使线上阶段也不复用该接口', async () => {
  mockTransport(botDetail({ engine: 'teclaw' }));
  await load('online');
  expect(containerCalled()).toBe(false);
});

it('Caller 配置请求不传 stage 参数，由后端按 Bot 当前发布状态判定', async () => {
  mockTransport(botDetail({}));
  await load('online');
  expect(callerContextParams()).not.toHaveProperty('stage');
});
