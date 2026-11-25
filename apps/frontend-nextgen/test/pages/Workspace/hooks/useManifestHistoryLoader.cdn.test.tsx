/** @jest-environment jsdom */
import { useManifestHistoryLoader } from '@/pages/Workspace/hooks/useManifestHistoryLoader';
import type { GroupChatProvider } from '@/services/workspace/groupChatProvider';
import type { ChatMessage } from '@tc-chat/core';
import { beforeEach, expect, it, jest } from '@jest/globals';
import { act, renderHook, waitFor } from '@testing-library/react';

// 副屏 CDN 双源桥：bare auto-mock（factory 不引用 jest，规避 TDZ）；运行时 require 取 jest.fn 断言。
jest.mock('@/services/bcs/libraryCdnInjector');
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const cdnModule: any = require('@/services/bcs/libraryCdnInjector');

/** 记录关键动作时序（注册必须先于 setMessages）。 */
const calls: string[] = [];

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function makeProvider(history: ChatMessage[]): any {
  return {
    liveEnabled: true,
    hasMoreHistory: false,
    beginHistoryHydration: () => calls.push('beginHistoryHydration'),
    connect: async () => calls.push('connect'),
    loadHistory: async () => {
      calls.push('loadHistory');
      return history;
    },
    enterLiveMode: () => calls.push('enterLiveMode'),
    disconnect: () => {},
  };
}

function senderMessage(botUuid: string): ChatMessage {
  return { extra: { botUuid } } as unknown as ChatMessage;
}

beforeEach(() => {
  jest.clearAllMocks();
  calls.length = 0;
  cdnModule.queryAndRegisterManifestLibraryCdn.mockImplementation(() => {
    calls.push('manifest');
    return Promise.resolve(1);
  });
  cdnModule.registerBotCdnList.mockImplementation(async (bots: Array<{ botId: string }>) => {
    calls.push(`registerBots:${bots.map((b) => b.botId).join('|')}`);
    return bots.length;
  });
});

it('历史挂载前完成 manifest + 发送方 Bot CDN 注册（按序断言）', async () => {
  const history = [senderMessage('rpp-bot:435979'), senderMessage('human_435979')];
  const setMessages = jest.fn((messages: ChatMessage[]) => calls.push(`setMessages:${messages.length}`));
  const onEnterOutcome = jest.fn();
  const provider = makeProvider(history) as unknown as GroupChatProvider;

  await act(async () => {
    renderHook(() =>
      useManifestHistoryLoader({
        provider,
        sessionId: 's1',
        historyRefreshNonce: 0,
        setHasMoreHistory: () => {},
        setIsLoadingMoreHistory: () => {},
        setMessages,
        onEnterOutcome,
      }),
    );
  });

  await waitFor(() => expect(onEnterOutcome).toHaveBeenCalledWith('ready', false));
  // 注册（manifest + 发送方）先于 setMessages；enterLiveMode 最后。
  expect(calls).toEqual([
    'beginHistoryHydration',
    'connect',
    'manifest',
    'loadHistory',
    'registerBots:rpp-bot',
    'setMessages:2',
    'enterLiveMode',
  ]);
  // 发送方集合含 owner（复合键完整），人类 actor 与空消息被剔除。
  expect(cdnModule.registerBotCdnList).toHaveBeenCalledWith([{ botId: 'rpp-bot', ownerId: '435979' }]);
});

it('Bot CDN 注册失败仍挂载历史（allSettled 降级，不阻塞会话就绪）', async () => {
  cdnModule.registerBotCdnList.mockRejectedValueOnce(new Error('boom'));
  const history = [senderMessage('rpp-bot:435979')];
  const setMessages = jest.fn();
  const onEnterOutcome = jest.fn();

  await act(async () => {
    renderHook(() =>
      useManifestHistoryLoader({
        provider: makeProvider(history) as unknown as GroupChatProvider,
        sessionId: 's1',
        historyRefreshNonce: 0,
        setHasMoreHistory: () => {},
        setIsLoadingMoreHistory: () => {},
        setMessages,
        onEnterOutcome,
      }),
    );
  });
  await waitFor(() => expect(onEnterOutcome).toHaveBeenCalledWith('ready', false));
  expect(setMessages).toHaveBeenCalledWith(history);
});

it('会话参与者 Bot 并入历史挂载前注册（覆盖 live 首条回复竞态）', async () => {
  const history = [senderMessage('rpp-bot:435979')];
  const setMessages = jest.fn((messages: ChatMessage[]) => calls.push(`setMessages:${messages.length}`));
  const onEnterOutcome = jest.fn();
  const props = {
    provider: makeProvider(history) as unknown as GroupChatProvider,
    sessionId: 's1',
    historyRefreshNonce: 0,
    // silencer 即将在 live 回复且历史里未发言——不预注册则首条回复副屏报「缺少 CDN 地址」。
    preRegisterBots: [{ botId: 'silencer', ownerId: '435979' }],
    setHasMoreHistory: () => {},
    setIsLoadingMoreHistory: () => {},
  };

  await act(async () => {
    renderHook(() => useManifestHistoryLoader({ ...props, setMessages, onEnterOutcome }));
  });
  await waitFor(() => expect(onEnterOutcome).toHaveBeenCalledWith('ready', false));
  // 参与者与历史发送方一起注册，且先于 setMessages。
  expect(cdnModule.registerBotCdnList).toHaveBeenCalledWith([
    { botId: 'rpp-bot', ownerId: '435979' },
    { botId: 'silencer', ownerId: '435979' },
  ]);
  expect(calls.indexOf('registerBots:rpp-bot|silencer')).toBeLessThan(calls.indexOf('setMessages:1'));
});
