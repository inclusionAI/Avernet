/** @jest-environment jsdom */
import type { ConversationBotView } from '@/domain/conversation/types';
import { ConversationCollapsedRail } from '@/pages/Workspace/Chat/components/ConversationCollapsedRail';
import { describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

const botOf = (
  botId: string,
  displayName: string,
  over: Partial<ConversationBotView['bot']> = {},
): ConversationBotView => ({
  bot: {
    botId,
    realBotId: botId,
    displayName,
    online: true,
    chatable: true,
    ...over,
  },
  section: 'managed',
});

const managedA = botOf('bot-a:1', '管理 Bot A');
const friendBot = { ...botOf('fb-1:2', '朋友 Bot'), section: 'friend' as const };

function renderRail(over: Partial<Parameters<typeof ConversationCollapsedRail>[0]> = {}) {
  const props = {
    sections: [
      { id: 'managed' as const, label: '我的 Bot', items: [managedA] },
      { id: 'friend' as const, label: '好友 Bot', items: [friendBot] },
    ],
    selectedBotId: null,
    selectedSection: null,
    onQuickOpen: jest.fn(),
    onExpand: jest.fn(),
    onOpenAgentCodingBot: jest.fn(),
    ...over,
  };
  render(<ConversationCollapsedRail {...props} />);
  return props;
}

describe('ConversationCollapsedRail（整栏收起的 Bot 泊位竖轨）', () => {
  it('renders quick-open tiles per section; friend online status shows in tooltip', () => {
    const props = renderRail();
    expect(screen.getByRole('button', { name: '打开 管理 Bot A' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '打开 朋友 Bot' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '打开 朋友 Bot' }));
    expect(props.onQuickOpen).toHaveBeenCalledWith(friendBot);
  });

  it('marks the selected bot with aria-current and keeps sections disambiguated', () => {
    renderRail({ selectedBotId: 'fb-1:2', selectedSection: 'friend' });
    expect(screen.getByRole('button', { name: '打开 朋友 Bot' })).toHaveAttribute('aria-current', 'true');
    expect(screen.getByRole('button', { name: '打开 管理 Bot A' })).not.toHaveAttribute('aria-current');
  });

  it('routes AgentCoding Bots to the coding chat and dims non-chatable ones', () => {
    const agentCoding = botOf('coding-bot:1', 'AgentCoding Bot', { isAgentCodingBot: true });
    const nonChatable = botOf('bot-nc:1', '禁聊 Bot', { chatable: false });
    const props = renderRail({
      sections: [
        { id: 'managed' as const, label: '我的 Bot', items: [agentCoding, nonChatable, managedA] },
      ],
    });
    fireEvent.click(screen.getByRole('button', { name: '打开 AgentCoding Bot' }));
    expect(props.onOpenAgentCodingBot).toHaveBeenCalledWith(agentCoding.bot);
    expect(props.onQuickOpen).not.toHaveBeenCalled();
    // 不可聊 Bot 保持降权占位：不提供直达按钮，仅身份标签。
    expect(screen.queryByRole('button', { name: '打开 禁聊 Bot' })).not.toBeInTheDocument();
    expect(screen.getByLabelText('禁聊 Bot（不可发起会话）')).toBeInTheDocument();
  });

  it('caps each section at five tiles and routes the +N overflow to expand', () => {
    const many = Array.from({ length: 7 }, (_, i) => botOf(`m-${i}:1`, `泊位 Bot ${i}`));
    const props = renderRail({ sections: [{ id: 'managed' as const, label: '我的 Bot', items: many }] });
    expect(screen.getByRole('button', { name: '打开 泊位 Bot 4' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '打开 泊位 Bot 5' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '展开侧栏，查看我的 Bot其余 2 个' }));
    expect(props.onExpand).toHaveBeenCalledTimes(1);
    expect(props.onQuickOpen).not.toHaveBeenCalled();
  });

  it('keeps empty sections as dashed ghost tiles that request expansion', () => {
    const props = renderRail({
      sections: [
        { id: 'managed' as const, label: '我的 Bot', items: [] },
        { id: 'friend' as const, label: '好友 Bot', items: [friendBot] },
      ],
    });
    fireEvent.click(screen.getByRole('button', { name: '展开侧栏，添加我的 Bot' }));
    expect(props.onExpand).toHaveBeenCalledTimes(1);
  });

  it('底端不再保留公开 Bot 发现入口（2026-10-14 体验修复）', () => {
    renderRail();
    expect(screen.queryByRole('button', { name: '前往公开 Bot' })).not.toBeInTheDocument();
  });
});

it.each([false, true])(
  '非运行中好友服务 Bot 收起侧栏后仍不可打开会话，并提示原因（Coding=%p）',
  async (isAgentCodingBot) => {
    const offlineFriend = botOf('offline:2', '未运行服务', {
      botType: 'service',
      onlineAvailable: false,
      chatable: false,
      isFriendBot: true,
      isAgentCodingBot,
    });
    offlineFriend.section = 'friend';
    const props = renderRail({ sections: [{ id: 'friend', label: '好友 Bot', items: [offlineFriend] }] });
    expect(screen.queryByRole('button', { name: '打开 未运行服务' })).not.toBeInTheDocument();
    const tile = screen.getByLabelText('未运行服务（不可发起会话）');
    fireEvent.click(tile);
    expect(props.onQuickOpen).not.toHaveBeenCalled();
    expect(props.onOpenAgentCodingBot).not.toHaveBeenCalled();
    fireEvent.pointerMove(tile, { pointerType: 'mouse' });
    await waitFor(() => expect(screen.getByRole('tooltip')).toHaveTextContent('非运行中服务 Bot'));
  },
);
