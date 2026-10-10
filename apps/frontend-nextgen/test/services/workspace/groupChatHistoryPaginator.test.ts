import type { DeliveryStatusView } from '@/domain/collaboration/types';
import { querySessionDeliveries } from '@/services/backendApi/collaboration/deliveryController';
import { listSessionMessages } from '@/services/backendApi/collaboration/sessionController';
import { GroupChatHistoryPaginator } from '@/services/workspace/groupChatHistoryPaginator';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';

jest.mock('@/services/backendApi/collaboration/sessionController');
jest.mock('@/services/backendApi/collaboration/deliveryController');

const mockedList = listSessionMessages as jest.MockedFunction<typeof listSessionMessages>;
const mockedQuery = querySessionDeliveries as jest.MockedFunction<typeof querySessionDeliveries>;

/** 后端返回新→旧降序；这里给出 [bot 回复(新), 用户消息(旧)]。 */
const pageDtos = [
  {
    id: 'a1',
    timestamp: 1700000002000,
    sender: 'bot-a',
    content: '',
    message_type: 'bot',
    role: 'assistant',
    run_id: 'run-1',
  },
  {
    id: 'u1',
    timestamp: 1700000001000,
    sender: 'user-1',
    content: '@ALL-Bots 大家好',
    message_type: 'human',
    role: 'user',
  },
] as never[];

const cancelledDelivery: DeliveryStatusView = {
  delivery_id: 'd-1',
  message_id: 'u1',
  target_bot_id: 'bot-a',
  flow_kind: 'group',
  kind: 'send',
  status: 'cancelled',
  state_version: 2,
  run_id: 'run-1',
  wait_reason: null,
  admission_error: null,
};

describe('GroupChatHistoryPaginator — 历史消息按投递终态标注 aborted', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('cancelled 投递的 run_id 命中的 assistant 消息返回 aborted 状态（线上真实形态：裸数组无信封）', async () => {
    mockedList.mockResolvedValue({ success: true, data: pageDtos } as never);
    // 线上真实响应：delivery 查询接口裸返回数组（网关不包装该组新路由）。
    mockedQuery.mockResolvedValue([cancelledDelivery]);

    const paginator = new GroupChatHistoryPaginator('s1', 'me');
    const messages = await paginator.loadLatest();

    expect(mockedQuery).toHaveBeenCalledWith('s1', ['u1']);
    const assistant = messages.find((m) => m.id === 'a1');
    expect(assistant?.status).toBe('aborted');
    const user = messages.find((m) => m.id === 'u1');
    expect(user?.status).toBe('history');
  });

  it('无 cancelled 投递时消息保持 history 状态', async () => {
    mockedList.mockResolvedValue({ success: true, data: pageDtos } as never);
    mockedQuery.mockResolvedValue([{ ...cancelledDelivery, status: 'completed' }]);

    const paginator = new GroupChatHistoryPaginator('s1', 'me');
    const messages = await paginator.loadLatest();

    expect(messages.find((m) => m.id === 'a1')?.status).toBe('history');
  });

  it('投递状态查询失败时降级返回未标注消息（不阻塞历史加载）', async () => {
    mockedList.mockResolvedValue({ success: true, data: pageDtos } as never);
    mockedQuery.mockRejectedValue(new Error('network down'));

    const paginator = new GroupChatHistoryPaginator('s1', 'me');
    const messages = await paginator.loadLatest();

    expect(messages.find((m) => m.id === 'a1')?.status).toBe('history');
  });

  it('页内无用户消息时不发起投递查询', async () => {
    mockedList.mockResolvedValue({
      success: true,
      data: [pageDtos[0]],
    } as never);

    const paginator = new GroupChatHistoryPaginator('s1', 'me');
    await paginator.loadLatest();

    expect(mockedQuery).not.toHaveBeenCalled();
  });
});
