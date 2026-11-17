/** @jest-environment jsdom */

import { act, renderHook } from '@testing-library/react';
import { BOT_COPY_FEATURE_ENABLED, useBotWorkshopCopyHost } from '@/hooks/useBotWorkshopCopyHost';
import { mapBotDto } from '@/services/botWorkshop/botMapper';

jest.mock('@/services/botWorkshop', () => ({
  botWorkshopService: { copyBot: jest.fn(), detail: jest.fn(), retryCopyBot: jest.fn() },
  BackendRequestError: class extends Error {},
}));

const editableOpenclawBot = mapBotDto({
  bot_id: 'bot-copy-1',
  bot_name: '可复制 Bot',
  engine: 'openclaw',
  status: 'ACTIVE',
  owner_id: 'u1',
  actions: ['view', 'edit', 'chat'],
}).item;

describe('useBotWorkshopCopyHost', () => {
  test('特性开启后为可编辑的 openclaw Bot 暴露复制入口并打开复制弹窗', () => {
    expect(BOT_COPY_FEATURE_ENABLED).toBe(true);
    const { result } = renderHook(() =>
      useBotWorkshopCopyHost({ load: jest.fn().mockResolvedValue(undefined), currentOpenApiUserId: 'u1' }),
    );

    expect(result.current.getCopyAvailability(editableOpenclawBot)).toEqual({
      visible: true,
      enabled: true,
    });

    act(() => result.current.openCopy(editableOpenclawBot));
    expect(result.current.copyBot?.id).toBe('bot-copy-1');
  });
});
