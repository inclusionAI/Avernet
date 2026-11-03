import { cancelDelivery } from '@/services/backendApi/collaboration/deliveryController';
import { queryWorkerTaskStatuses } from '@/services/backendApi/collaboration/workerTaskController';
import { cancelQueuedDelivery, queryWorkerTasks } from '@/services/workspace/messageDeliveryService';
import { describe, expect, it, jest } from '@jest/globals';

jest.mock('@/services/backendApi/collaboration/deliveryController');
jest.mock('@/services/backendApi/collaboration/workerTaskController');

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
    expect(outcome.clientMessageIds).toEqual([]);
    expect(outcome.canonicalMessageIds).toEqual([]);
  });

  it('接口异常向上抛出（由 Hook 统一 toast）', async () => {
    mockedCancel.mockRejectedValue(new Error('network'));

    await expect(cancelQueuedDelivery('m1', 'd1', 's1')).rejects.toThrow('network');
  });
  it('只汇总成功响应中的非空精确关联 ID，去重并兼容旧服务端', async () => {
    const delivery = { delivery_id: 'd1', message_id: 'canonical-1' } as never;
    mockedCancel.mockResolvedValue([
      { delivery, error: null, client_message_id: 'client-1' },
      { delivery, error: null, client_message_id: 'client-1' },
      { delivery: { message_id: 'error-canonical' } as never, error: 'delivery_state_changed', client_message_id: 'do-not-remove' },
      { delivery: { message_id: 'unconfirmed-canonical' } as never, error: null, client_message_id: null },
      { delivery, error: null, client_message_id: '' },
      { delivery, error: null },
    ]);
    const outcome = await cancelQueuedDelivery('m1', 'd1', 's1');
    expect(outcome.clientMessageIds).toEqual(['client-1']);
    expect(outcome.canonicalMessageIds).toEqual(['canonical-1']);
    expect(outcome.firstError).toBe('delivery_state_changed');
  });

});


it('batches complete task identities in groups of 20 and deduplicates selectors', async () => {
  const query = jest.mocked(queryWorkerTaskStatuses);
  query.mockImplementation(async (refs: string[]) => ({ tasks: refs.map((ref: string) => ({ ref, status: 'completed' as const })) }));
  const refs = Array.from({ length: 41 }, (_, i) => `bcs_intent_${i.toString(16).padStart(32, '0')}`);
  const result = await queryWorkerTasks([...refs, refs[0]]);
  expect(query.mock.calls.map(([batch]: [string[]]) => batch.length)).toEqual([20, 20, 1]);
  expect(result.map((row) => row.ref)).toEqual(refs);
});
