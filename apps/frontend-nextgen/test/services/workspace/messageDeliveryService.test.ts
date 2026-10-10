import { cancelDelivery } from '@/services/backendApi/collaboration/deliveryController';
import { cancelQueuedDelivery } from '@/services/workspace/messageDeliveryService';
import { describe, expect, it, jest } from '@jest/globals';

jest.mock('@/services/backendApi/collaboration/deliveryController');

const mockedCancel = cancelDelivery as jest.MockedFunction<typeof cancelDelivery>;

describe('messageDeliveryService.cancelQueuedDelivery', () => {
  it('透传取消结果并汇总首个失败原因', async () => {
    mockedCancel.mockResolvedValue([
      { delivery: { delivery_id: 'd1' } as never, error: null },
      { delivery: { delivery_id: 'd2' } as never, error: '该 Bot 离线' },
    ]);

    const outcome = await cancelQueuedDelivery('m1', 'd1', 's1');

    expect(mockedCancel).toHaveBeenCalledWith('m1', 'd1', 's1');
    expect(outcome.results).toHaveLength(2);
    expect(outcome.firstError).toBe('该 Bot 离线');
  });

  it('全部成功时 firstError 为 null', async () => {
    mockedCancel.mockResolvedValue([{ delivery: { delivery_id: 'd1' } as never, error: null }]);

    const outcome = await cancelQueuedDelivery('m1', 'd1', 's1');

    expect(outcome.firstError).toBeNull();
  });

  it('接口异常向上抛出（由 Hook 统一 toast）', async () => {
    mockedCancel.mockRejectedValue(new Error('network'));

    await expect(cancelQueuedDelivery('m1', 'd1', 's1')).rejects.toThrow('network');
  });
});
