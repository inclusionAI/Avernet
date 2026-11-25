import { querySessionDeliveries } from '@/services/backendApi/collaboration/deliveryController';
import { queryTaskDeliveries } from '@/services/workspace/messageDeliveryService';

jest.mock('@/services/backendApi/collaboration/deliveryController', () => ({ querySessionDeliveries: jest.fn() }));

it('recovers known tasks in bounded batches and excludes ordinary deliveries', async () => {
  const query = jest.mocked(querySessionDeliveries);
  query.mockResolvedValue([{ delivery_id: 'ordinary' }, { delivery_id: 'task', task: { task_ref: 'T-1' } }] as never);
  const ids = Array.from({ length: 201 }, (_, index) => `source-${index}`);
  const rows = await queryTaskDeliveries('session', ids);
  expect(query.mock.calls.map((call) => call[1].length)).toEqual([100, 100, 1]);
  expect(query.mock.calls.flatMap((call) => call[1])).toEqual(ids);
  expect(rows.every((row) => row.task?.task_ref === 'T-1')).toBe(true);
});
