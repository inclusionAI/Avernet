/** @jest-environment jsdom */
import { defaultCapabilities, extendCapabilities } from '@/capabilities';
import { CodingBotStageBoundary } from '@/pages/Workspace/Chat/CodingBotStageBoundary';
import type { BotChatSessionView, ChatBotView } from '@/services/workspace/botSessionService';
import { resolveServiceBotPublishSelection } from '@/services/workspace/serviceBotPublishResolver';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { render, screen, waitFor } from '@testing-library/react';

jest.mock('@/services/workspace/serviceBotPublishResolver', () => ({
  resolveServiceBotPublishSelection: jest.fn(),
}));

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
  jest.clearAllMocks();
});

describe('CodingBotStageBoundary', () => {
  it('Open Core capability 未注入时显式 fail-closed，不回退普通 Chat Runtime', () => {
    render(<CodingBotStageBoundary bot={bot} session={session} human={human} />);

    expect(screen.getByText('Coding Bot 对话暂不可用')).toBeInTheDocument();
    expect(screen.getByText('当前部署未配置 Coding Bot 对话组件，请联系管理员开通后重试。')).toBeInTheDocument();
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
    const [stageProps] = Stage.mock.calls as unknown as Array<[Record<string, unknown>, unknown]>;
    expect(stageProps).not.toHaveProperty('stage');
    expect(stageProps).not.toHaveProperty('publishId');
  });

  it('online 服务 Coding Bot 通过生命周期接口成对传递 publishId 与 stage，避免回退草稿', async () => {
    const Stage = jest.fn(() => <div data-testid="injected-coding-stage" />);
    extendCapabilities({
      getCodingBotConversationHost: () => ({
        status: 'available',
        value: { Stage },
      }),
    });
    const serviceBot: ChatBotView = {
      ...bot,
      botType: 'service',
      runtimeStage: 'online',
    };
    (resolveServiceBotPublishSelection as jest.Mock).mockResolvedValue({
      publishId: '42',
      stage: 'online',
    });

    render(<CodingBotStageBoundary bot={serviceBot} session={session} human={human} />);

    await waitFor(() => expect(screen.getByTestId('injected-coding-stage')).toBeInTheDocument());
    expect(resolveServiceBotPublishSelection).toHaveBeenCalledWith({
      botId: 'coding-bot',
      ownerId: '2088',
      stage: 'online',
    });
    expect(Stage).toHaveBeenCalledWith(
      expect.objectContaining({
        botId: 'coding-bot',
        sessionId: 'session-1',
        publishId: '42',
        stage: 'online',
      }),
      expect.anything(),
    );
    expect(Stage).toHaveBeenCalledTimes(1);
  });

  it('线上服务发布解析中复用鱼骨屏，不再出现独立说明页', async () => {
    const Stage = jest.fn(() => <div data-testid="injected-coding-stage" />);
    extendCapabilities({
      getCodingBotConversationHost: () => ({
        status: 'available',
        value: { Stage },
      }),
    });
    const serviceBot: ChatBotView = {
      ...bot,
      botType: 'service',
      runtimeStage: 'online',
    };
    let resolveSelection!: (selection: { publishId: string; stage: 'online' }) => void;
    (resolveServiceBotPublishSelection as jest.Mock).mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveSelection = resolve;
        }),
    );

    render(<CodingBotStageBoundary bot={serviceBot} session={session} human={human} />);

    expect(screen.queryByText('正在解析服务发布信息')).not.toBeInTheDocument();
    expect(screen.getByRole('status', { name: 'Coding Bot 对话加载中' })).toBeInTheDocument();
    expect(Stage).not.toHaveBeenCalled();

    resolveSelection({ publishId: '129', stage: 'online' });
    await waitFor(() => expect(screen.getByTestId('injected-coding-stage')).toBeInTheDocument());
    expect(screen.queryByRole('status', { name: 'Coding Bot 对话加载中' })).not.toBeInTheDocument();
    expect(Stage).toHaveBeenCalledWith(
      expect.objectContaining({ publishId: '129', stage: 'online' }),
      expect.anything(),
    );
    expect(Stage).toHaveBeenCalledTimes(1);
  });

  it('团队服务 Coding Bot 按原始 owner 地址解析线上发布', async () => {
    const Stage = jest.fn(() => <div data-testid="injected-coding-stage" />);
    extendCapabilities({
      getCodingBotConversationHost: () => ({
        status: 'available',
        value: { Stage },
      }),
    });
    const teamBot: ChatBotView = {
      ...bot,
      botId: 'coding-bot:proj:8',
      realBotId: 'coding-bot',
      ownerId: 'proj:8',
      botType: 'service',
      runtimeStage: 'online',
      isTeamBot: true,
    };
    (resolveServiceBotPublishSelection as jest.Mock).mockResolvedValue({
      publishId: '77',
      stage: 'online',
    });

    render(<CodingBotStageBoundary bot={teamBot} session={session} human={human} />);

    await waitFor(() => expect(screen.getByTestId('injected-coding-stage')).toBeInTheDocument());
    expect(resolveServiceBotPublishSelection).toHaveBeenCalledWith({
      botId: 'coding-bot',
      ownerId: 'proj:8',
      stage: 'online',
    });
    expect(Stage).toHaveBeenCalledWith(
      expect.objectContaining({
        botId: 'coding-bot',
        ownerId: 'proj:8',
        userId: '382641',
        publishId: '77',
        stage: 'online',
      }),
      expect.anything(),
    );
  });

  it('线上 lifecycle 中没有 running 发布卡时不挂载 Embedded，也不回退草稿', async () => {
    const Stage = jest.fn(() => <div data-testid="injected-coding-stage" />);
    extendCapabilities({
      getCodingBotConversationHost: () => ({
        status: 'available',
        value: { Stage },
      }),
    });
    const serviceBot: ChatBotView = {
      ...bot,
      botType: 'service',
      runtimeStage: 'online',
    };
    (resolveServiceBotPublishSelection as jest.Mock).mockRejectedValue(
      new Error('未找到可用的线上服务发布单'),
    );

    render(<CodingBotStageBoundary bot={serviceBot} session={session} human={human} />);

    await waitFor(() =>
      expect(screen.getByText('服务运行环境解析失败')).toBeInTheDocument(),
    );
    expect(Stage).not.toHaveBeenCalled();
  });

  it('零会话时仍挂载 Embedded 宿主，并显式传递 undefined sessionId', () => {
    const Stage = jest.fn(() => <div data-testid="injected-coding-stage" />);
    extendCapabilities({
      getCodingBotConversationHost: () => ({
        status: 'available',
        value: { Stage },
      }),
    });

    render(<CodingBotStageBoundary bot={bot} session={null} human={human} />);

    expect(screen.getByTestId('injected-coding-stage')).toBeInTheDocument();
    expect(Stage).toHaveBeenCalledWith(
      expect.objectContaining({ botId: 'coding-bot', sessionId: undefined }),
      expect.anything(),
    );
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
