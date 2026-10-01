/** @jest-environment jsdom */
import { useBotCapabilityDetail } from '@/hooks/useBotCapabilityDetail';
import {
  botCapabilityDetailService,
  type CapabilityDetailTarget,
} from '@/services/botWorkshop/botCapabilityDetailService';
import { renderHook, waitFor } from '@testing-library/react';

jest.mock('@/services/botWorkshop/botCapabilityDetailService', () => ({
  botCapabilityDetailService: { load: jest.fn() },
}));

test('详情未打开不请求，选择后才读取并携带 Owner', async () => {
  const load = botCapabilityDetailService.load as jest.Mock;
  load.mockResolvedValue({ name: '报告', description: '', content: '# 内容', tools: [] });
  const { result, rerender } = renderHook(
    ({ target }: { target?: CapabilityDetailTarget }) => useBotCapabilityDetail('bot-1', target, 'owner-1'),
    { initialProps: { target: undefined } as { target?: CapabilityDetailTarget } },
  );
  expect(load).not.toHaveBeenCalled();
  rerender({ target: { kind: 'skill', id: '42', name: '报告' } });
  await waitFor(() => expect(result.current.detail?.content).toBe('# 内容'));
  expect(load).toHaveBeenCalledTimes(1);
  expect(load).toHaveBeenCalledWith('bot-1', { kind: 'skill', id: '42', name: '报告' }, 'owner-1');
});
