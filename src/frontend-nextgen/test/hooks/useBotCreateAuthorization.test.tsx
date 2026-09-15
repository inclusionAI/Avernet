/** @jest-environment jsdom */

import { useBotCreateAuthorization } from '@/hooks/useBotCreateAuthorization';
import type { BotCreateAuthorization } from '@/services/botWorkshop';
import { botWorkshopService } from '@/services/botWorkshop';
import { act, renderHook, waitFor } from '@testing-library/react';

jest.mock('@/services/botWorkshop', () => ({
  botWorkshopService: { pollCreateAuthorization: jest.fn() },
}));
jest.mock('sonner', () => ({ toast: { warning: jest.fn() } }));

const authorization: BotCreateAuthorization = {
  type: 'authorization_required',
  botId: 'bot-pending',
  iframeUrl: 'https://agentpass.example/authorize',
  redirectUrl: '',
  request: {
    bot_name: '测试 Bot',
    bot_desc: '',
    engine: 'openclaw',
    cluster_name: 'ACRA',
    bot_type: 'personal',
  },
};

afterEach(() => jest.clearAllMocks());

test('用户拒绝授权时退出授权态并回到列表，不触发创建成功刷新', async () => {
  const onCreated = jest.fn();
  const onTerminated = jest.fn();
  (botWorkshopService.pollCreateAuthorization as jest.Mock).mockResolvedValue({
    status: 'REJECTED',
    message: 'User rejected authorization',
  });
  const { result } = renderHook(() => useBotCreateAuthorization(onCreated, onTerminated));

  act(() => result.current.beginAuthorization(authorization));

  await waitFor(() => expect(result.current.authorization).toBeUndefined());
  expect(onTerminated).toHaveBeenCalledWith('REJECTED', 'User rejected authorization');
  expect(onCreated).not.toHaveBeenCalled();
});
