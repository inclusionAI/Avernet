/** @jest-environment jsdom */
import { useSenderBotCdnSync } from '@/pages/Workspace/hooks/useSenderBotCdnSync';
import type { ChatMessage } from '@tc-chat/core';
import { beforeEach, expect, it, jest } from '@jest/globals';
import { act, renderHook } from '@testing-library/react';

// 副屏 CDN 双源桥：bare auto-mock（factory 不引用 jest，规避 TDZ）；运行时 require 取 jest.fn 断言。
jest.mock('@/services/bcs/libraryCdnInjector');
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const cdnModule: any = require('@/services/bcs/libraryCdnInjector');

function senderMessage(botUuid: string): ChatMessage {
  return { extra: { botUuid } } as unknown as ChatMessage;
}

interface SyncProps {
  sessionId: string | null;
  messages: ChatMessage[];
  participants?: Array<{ actorId: string; kind?: 'human' | 'bot' }>;
}

function renderSync(initialProps: SyncProps) {
  return renderHook(({ sessionId, messages, participants }: SyncProps) => useSenderBotCdnSync(sessionId, messages, participants), {
    initialProps,
  });
}

beforeEach(() => {
  jest.clearAllMocks();
  cdnModule.hasBotCdnBucket.mockReturnValue(false);
  cdnModule.registerBotCdnList.mockImplementation(async () => 0);
});

it('无发送方（空消息/仅人类 actor）不触发注册', () => {
  renderSync({ sessionId: 's1', messages: [] });
  expect(cdnModule.registerBotCdnList).not.toHaveBeenCalled();
  // 会话未进入（sessionId null）同样不注册。
  renderSync({ sessionId: null, messages: [senderMessage('bot-a:1')] });
  expect(cdnModule.registerBotCdnList).not.toHaveBeenCalled();
});

it('live 新消息出现未注册发送方时触发一次增量注册', async () => {
  const utils = renderSync({ sessionId: 's1', messages: [] });
  expect(cdnModule.registerBotCdnList).not.toHaveBeenCalled();

  await act(async () => {
    utils.rerender({ sessionId: 's1', messages: [senderMessage('invited-bot:435979')] });
  });
  expect(cdnModule.registerBotCdnList).toHaveBeenCalledTimes(1);
  expect(cdnModule.registerBotCdnList).toHaveBeenCalledWith([{ botId: 'invited-bot', ownerId: '435979' }]);
});

it('同一 Bot 连续消息不重复请求（复合 key 缓存命中）', async () => {
  const utils = renderSync({ sessionId: 's1', messages: [senderMessage('bot-a:100001')] });
  expect(cdnModule.registerBotCdnList).toHaveBeenCalledTimes(1);
  await act(async () => {
    utils.rerender({ sessionId: 's1', messages: [senderMessage('bot-a:100001'), senderMessage('bot-a:100001')] });
  });
  expect(cdnModule.registerBotCdnList).toHaveBeenCalledTimes(1);
});

it('桶已存在（含负缓存）的 Bot 不触发请求', async () => {
  cdnModule.hasBotCdnBucket.mockReturnValue(true);
  await act(async () => {
    renderSync({ sessionId: 's1', messages: [senderMessage('bot-a:100001')] });
  });
  expect(cdnModule.registerBotCdnList).not.toHaveBeenCalled();
});

it('会话到达时即注册参与者 Bot（历史未发言成员，先于 live 首条回复）', async () => {
  // 模拟真实负缓存：已注册桶（含负缓存）不再出现在 pending 中。
  const known = new Set<string>();
  cdnModule.hasBotCdnBucket.mockImplementation(
    (botId: string, ownerId?: string) => known.has(`${botId}:${ownerId ?? ''}`),
  );
  cdnModule.registerBotCdnList.mockImplementation(async (bots: Array<{ botId: string; ownerId?: string }>) => {
    for (const bot of bots) known.add(`${bot.botId}:${bot.ownerId ?? ''}`);
    return bots.length;
  });

  const utils = renderSync({
    sessionId: 's1',
    messages: [senderMessage('spoken-bot:435979')],
    participants: [{ actorId: 'silencer:435979', kind: 'bot' }],
  });
  expect(cdnModule.registerBotCdnList).toHaveBeenCalledTimes(1);
  expect(cdnModule.registerBotCdnList).toHaveBeenCalledWith([
    { botId: 'spoken-bot', ownerId: '435979' },
    { botId: 'silencer', ownerId: '435979' },
  ]);

  // 参与者详情晚到（会话详情补齐）同样触发一次增量注册，只补新增成员。
  await act(async () => {
    utils.rerender({
      sessionId: 's1',
      messages: [senderMessage('spoken-bot:435979')],
      participants: [{ actorId: 'silencer:435979', kind: 'bot' }, { actorId: 'latecomer:100002', kind: 'bot' }],
    });
  });
  expect(cdnModule.registerBotCdnList).toHaveBeenCalledTimes(2);
  expect(cdnModule.registerBotCdnList).toHaveBeenLastCalledWith([{ botId: 'latecomer', ownerId: '100002' }]);
});

it('参与者与发送方同一 Bot（同复合键）只入一次注册', async () => {
  await act(async () => {
    renderSync({
      sessionId: 's1',
      messages: [senderMessage('bot-a:100001')],
      participants: [{ actorId: 'bot-a:100001', kind: 'bot' }],
    });
  });
  // 合并去重后单次注册，且不重复出现在 pending 集合中。
  expect(cdnModule.registerBotCdnList).toHaveBeenCalledTimes(1);
  expect(cdnModule.registerBotCdnList).toHaveBeenCalledWith([{ botId: 'bot-a', ownerId: '100001' }]);
});
