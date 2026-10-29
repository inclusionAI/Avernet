/** @jest-environment jsdom */
import { CodingBotStageBoundary } from '@/pages/Workspace/Chat/CodingBotStageBoundary';
import type { BotChatSessionView, ChatBotView } from '@/services/workspace/botSessionService';
import { afterEach, describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { render, screen } from '@testing-library/react';
import { defaultCapabilities, extendCapabilities } from '@/capabilities';

const bot: ChatBotView = {
  botId: 'coding-bot:2088',
  realBotId: 'coding-bot',
  ownerId: '2088',
  displayName: 'Coding Bot',
  online: true,
  chatable: true,
  isAgentCodingBot: true,
};

const human = {
  userId: '382641',
  displayName: 'Tester',
  online: true,
};

const session: BotChatSessionView = {
  sessionId: 'session-1',
  botId: 'coding-bot:2088',
  title: 'Coding 会话',
  messageCount: 0,
  gmtCreate: '',
  gmtModified: '',
};

afterEach(() => {
  extendCapabilities(defaultCapabilities);
});

describe('CodingBotStageBoundary', () => {
  it('Open Core capability 未注入时显式 fail-closed，不回退普通 Chat Runtime', () => {
    render(<CodingBotStageBoundary bot={bot} session={session} human={human} />);

    expect(screen.getByText('Coding Bot 对话暂不可用')).toBeInTheDocument();
    expect(
      screen.getByText('当前部署未配置 Coding Bot 对话组件，请联系管理员开通后重试。'),
    ).toBeInTheDocument();
    expect(screen.queryByText('Coding 会话')).not.toBeInTheDocument();
  });

  it('登录身份未就绪时暂不挂载 AIXCoding 子应用', () => {
    render(<CodingBotStageBoundary bot={bot} session={session} human={null} />);

    expect(screen.getByText('登录身份加载中')).toBeInTheDocument();
    expect(screen.queryByTestId('injected-coding-stage')).not.toBeInTheDocument();
  });

  it('internal capability 注入时把当前 Bot 与 Session 输入传给 Embedded 宿主', () => {
    const Stage = jest.fn(() => <div data-testid="injected-coding-stage" />);
    extendCapabilities({
      getCodingBotConversationHost: () => ({
        status: 'available',
        value: { Stage },
      }),
    });

    render(<CodingBotStageBoundary bot={bot} session={session} human={human} />);

    expect(screen.getByTestId('injected-coding-stage')).toBeInTheDocument();
    expect(Stage).toHaveBeenCalledWith(
      expect.objectContaining({
        botId: 'coding-bot',
        sessionId: 'session-1',
        ownerId: '2088',
        userId: '382641',
        nickName: 'Tester',
        mode: 'embed',
      }),
      expect.anything(),
    );
    expect(Stage.mock.calls[0][0]).not.toHaveProperty('stage');
    expect(Stage.mock.calls[0][0]).not.toHaveProperty('publishId');
  });

  it('Session 切换只更新 Stage props，不卸载重建宿主', () => {
    const Stage = jest.fn(() => <div data-testid="injected-coding-stage" />);
    extendCapabilities({
      getCodingBotConversationHost: () => ({
        status: 'available',
        value: { Stage },
      }),
    });

    const secondSession = { ...session, sessionId: 'session-2' };
    const { rerender } = render(<CodingBotStageBoundary bot={bot} session={session} human={human} />);
    rerender(<CodingBotStageBoundary bot={bot} session={secondSession} human={human} />);

    expect(Stage).toHaveBeenCalledTimes(2);
    expect(Stage).toHaveBeenLastCalledWith(
      expect.objectContaining({ botId: 'coding-bot', sessionId: 'session-2' }),
      expect.anything(),
    );
    expect(screen.getByTestId('injected-coding-stage')).toBeInTheDocument();
  });
});
