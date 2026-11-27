/** @jest-environment jsdom */
import { useManifestHistoryLoader } from '@/pages/Workspace/hooks/useManifestHistoryLoader';
import { queryAndRegisterManifestLibraryCdn, registerBotCdnList } from '@/services/bcs/libraryCdnInjector';
import type { GroupChatProvider } from '@/services/workspace/groupChatProvider';
import type { ChatMessage } from '@tc-chat/core';
import { act, renderHook, waitFor } from '@testing-library/react';

jest.mock('@/services/bcs/libraryCdnInjector');

it('并行等待 manifest 与 Provider 历史就绪后挂载，不重复查询发送方或成员 Bot', async () => {
  let finishManifest!: (count: number) => void;
  let finishHistory!: (messages: ChatMessage[]) => void;
  jest.mocked(queryAndRegisterManifestLibraryCdn).mockReturnValue(new Promise((resolve) => { finishManifest = resolve; }));
  const history: ChatMessage[] = [{ id: '1', role: 'assistant', status: 'done', content: 'hello', extra: { botUuid: 'bot-a:1' } }];
  const provider = {
    liveEnabled: true,
    hasMoreHistory: false,
    beginHistoryHydration: jest.fn(),
    connect: jest.fn().mockResolvedValue(undefined),
    loadHistory: jest.fn().mockReturnValue(new Promise((resolve) => { finishHistory = resolve; })),
    enterLiveMode: jest.fn(),
    disconnect: jest.fn(),
  };
  const setMessages = jest.fn();
  const onEnterOutcome = jest.fn();
  renderHook(() => useManifestHistoryLoader({
    provider: provider as unknown as GroupChatProvider,
    sessionId: 's1', historyRefreshNonce: 0,
    setHasMoreHistory: jest.fn(), setIsLoadingMoreHistory: jest.fn(), setMessages, onEnterOutcome,
  }));
  await waitFor(() => expect(provider.loadHistory).toHaveBeenCalled());
  expect(queryAndRegisterManifestLibraryCdn).toHaveBeenCalledTimes(1);
  await act(async () => { finishManifest(1); });
  expect(setMessages).not.toHaveBeenCalled();
  await act(async () => { finishHistory(history); });
  expect(setMessages).toHaveBeenCalledWith(history);
  expect(provider.enterLiveMode).toHaveBeenCalled();
  expect(onEnterOutcome).toHaveBeenCalledWith('ready', false);
  expect(registerBotCdnList).not.toHaveBeenCalled();
});
