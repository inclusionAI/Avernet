import { loadRoutineContext } from '@/services/botScheduledTasks/metadata';
import { sendRoutineRequest } from '@/services/botScheduledTasks/http';
import type { RoutineStage } from '@/services/botScheduledTasks/types';

jest.mock('@/services/botScheduledTasks/http', () => ({ sendRoutineRequest: jest.fn() }));

// /containers 只报告线上容器：草稿/预发阶段（stage≠online）注定查空，必须跳过。
// 详情接口的 payload 不含发布状态字段，线上判定依赖链接查询参数 runtime_stage。
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

it('草稿阶段不请求线上容器清单', async () => {
  mockTransport(botDetail({}));
  const context = await load('draft');
  expect(containerCalled()).toBe(false);
  expect(context.environments[2].instances).toEqual([]);
});

it('预发阶段不请求线上容器清单', async () => {
  mockTransport(botDetail({}));
  await load('verify');
  expect(containerCalled()).toBe(false);
});

it('链接未携带阶段时不请求线上容器清单', async () => {
  mockTransport(botDetail({}));
  await load();
  expect(containerCalled()).toBe(false);
});

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
