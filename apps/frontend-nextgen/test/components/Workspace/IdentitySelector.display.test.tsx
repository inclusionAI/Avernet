/** @jest-environment jsdom */
/**
 * IdentitySelector 测试拆分（display 分册）：加载/空态与身份信息展示类用例。
 *
 * 拆分背景：原单文件 17 用例串行约 188s、全量并行下随机超时。根因经探针定位——
 * Jest+jsdom+Radix 弹层交互的框架盲区：每个「打开过 Popover/Tooltip」的用例在收尾
 * 阶段固定多背约 12s（用例本体 <0.1s、unmount 1ms、句柄探测无泄漏、forceExit 无效）。
 * 治标方案：按主题拆分套件，让弹层用例的长尾分摊到并行 worker，单文件时长可控。
 * 用例与断言逐字保留，仅做文件级拆分（零断言语义变化）。
 */
import { extendCapabilities } from '@/capabilities';
import { WorkspaceIdentitySelector } from '@/components/Workspace/IdentitySelector';
import { botRegistrationService, resolveBcsEndpoint } from '@/services/workspace/botRegistrationService';
import type { Identity } from '@/services/workspace/workspaceModel';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen } from '@testing-library/react';

// 原文件口径：jsdom 下 Radix 异步渲染开销大，保留 60s 上限（仅本文件生效）。
jest.setTimeout(60000);

jest.mock('@/services/workspace/botRegistrationService');

const getRegistrationTokenMock = botRegistrationService.getRegistrationToken as jest.MockedFunction<
  typeof botRegistrationService.getRegistrationToken
>;
const resolveBcsEndpointMock = resolveBcsEndpoint as jest.MockedFunction<typeof resolveBcsEndpoint>;

const identities: Identity[] = [
  {
    id: 'human_900004',
    name: '示例用户',
    kind: 'user',
    avatar: '风',
  },
  {
    id: 'bot-online',
    name: '协作 Bot',
    kind: 'bot',
    avatar: 'B',
    engine: 'OpenClaw',
    botType: 'personal',
    status: 'available',
    chatStatus: 'online',
    reachability: 'reachable',
  },
  {
    id: 'bot-hidden',
    name: '隐藏 Bot',
    kind: 'bot',
    avatar: 'H',
    engine: 'ClaudeCode',
    botType: 'service',
    status: 'unavailable',
    chatStatus: 'hidden',
    reachability: 'unreachable',
  },
];

describe('WorkspaceIdentitySelector 身份信息展示', () => {
  beforeEach(() => {
    extendCapabilities({
      getBotRegistrationEnabled: () => ({ status: 'available', value: true }),
    });
    getRegistrationTokenMock.mockReset();
    resolveBcsEndpointMock.mockReset();
    resolveBcsEndpointMock.mockReturnValue('http://127.0.0.1:21000');
  });

  it('loading uses a spinner row instead of a temporary empty-state copy', () => {
    render(<WorkspaceIdentitySelector identities={[]} activeId={null} onChange={() => {}} identityListLoading />);

    expect(screen.getByRole('button', { name: '协作身份加载中' })).toBeInTheDocument();
    expect(screen.queryByText('暂无可协作身份')).not.toBeInTheDocument();
  });

  it('error 态提供 onRetry 时展示页内重试按钮并触发回调（AC-6）', () => {
    const onRetry = jest.fn();
    render(
      <WorkspaceIdentitySelector
        identities={[]}
        activeId={null}
        onChange={() => {}}
        identityStatus="error"
        onRetry={onRetry}
      />,
    );

    expect(screen.getByText('身份加载失败')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '重试' }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it('error 态未提供 onRetry 时保持原空态文案（左上角等既有消费方不变）', () => {
    render(<WorkspaceIdentitySelector identities={[]} activeId={null} onChange={() => {}} identityStatus="error" />);

    expect(screen.getByText('暂无可协作身份，请刷新重试')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '重试' })).not.toBeInTheDocument();
  });

  it('不把 TeamClaw 网关等 provider 名称当作引擎标签展示', () => {
    render(
      <WorkspaceIdentitySelector
        identities={[{ id: 'provider-bot', name: 'Provider Bot', kind: 'bot', avatar: 'P', engine: 'TeamClaw网关' }]}
        activeId="provider-bot"
        onChange={() => {}}
      />,
    );

    expect(screen.getByText('引擎类型暂无')).toBeInTheDocument();
    expect(screen.queryByText('TeamClaw网关')).not.toBeInTheDocument();
  });

  it('用户身份使用顶栏头像，并不展示引擎和 Bot 状态，保留用户标签', () => {
    render(
      <WorkspaceIdentitySelector
        identities={identities}
        activeId="human_900004"
        userAvatarUrl="https://cdn.example.com/avatar.png"
        onChange={() => {}}
      />,
    );

    expect(screen.getByRole('img', { name: '示例用户' })).toHaveAttribute('src', 'https://cdn.example.com/avatar.png');
    expect(screen.getByText('用户')).toBeInTheDocument();
    expect(screen.getByText('工号：900004')).toBeInTheDocument();
    expect(screen.queryByText('OpenClaw')).not.toBeInTheDocument();
    expect(screen.queryByText('可参与群聊：')).not.toBeInTheDocument();
  });

  it('只有一个协作身份时不显示切换提示', () => {
    render(<WorkspaceIdentitySelector identities={[identities[0]]} activeId="human_900004" onChange={() => {}} />);

    expect(screen.queryByText('可切换身份')).not.toBeInTheDocument();
  });

  it('不可达 Bot 显示离线，且不展示群聊参与状态', async () => {
    render(
      <WorkspaceIdentitySelector
        identities={[
          {
            id: 'bot-unreachable',
            name: '暂时不可达 Bot',
            kind: 'bot',
            avatar: 'B',
            engine: 'Hermes',
            botType: 'desktop',
            chatStatus: 'online',
            reachability: 'unreachable',
          },
        ]}
        activeId="bot-unreachable"
        onChange={() => {}}
      />,
    );

    expect(screen.getByText('离线')).toBeInTheDocument();
    expect(screen.queryByText(/可参与群聊/)).not.toBeInTheDocument();
    expect(screen.getByText('桌面 Bot')).toBeInTheDocument();
    fireEvent.pointerMove(screen.getByLabelText('Bot 离线'));
    expect(await screen.findByText('请检查 Bot 实例状态')).toBeInTheDocument();
  });

  it('可达 Bot 即使 chatStatus 缺省也优先显示在线', () => {
    render(
      <WorkspaceIdentitySelector
        identities={[
          {
            id: 'bot-offline',
            name: '离线 Bot',
            kind: 'bot',
            avatar: 'B',
            chatStatus: 'hidden',
            reachability: 'reachable',
          },
        ]}
        activeId="bot-offline"
        onChange={() => {}}
      />,
    );

    expect(screen.getByText('在线')).toBeInTheDocument();
    expect(screen.queryByText(/可参与群聊/)).not.toBeInTheDocument();
  });
});
