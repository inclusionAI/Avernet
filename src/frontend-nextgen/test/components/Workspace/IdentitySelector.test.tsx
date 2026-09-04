/** @jest-environment jsdom */
import { extendCapabilities } from '@/capabilities';
import { WorkspaceIdentitySelector } from '@/components/Workspace/IdentitySelector';
import { botRegistrationService, resolveBcsEndpoint } from '@/services/workspace/botRegistrationService';
import type { Identity } from '@/services/workspace/workspaceModel';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

jest.mock('@/services/workspace/botRegistrationService');

const getRegistrationTokenMock = botRegistrationService.getRegistrationToken as jest.MockedFunction<
  typeof botRegistrationService.getRegistrationToken
>;
const resolveBcsEndpointMock = resolveBcsEndpoint as jest.MockedFunction<typeof resolveBcsEndpoint>;

const identities: Identity[] = [
  {
    id: 'human_447147',
    name: '风太',
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

describe('WorkspaceIdentitySelector', () => {
  beforeEach(() => {
    extendCapabilities({
      getBotRegistrationEnabled: () => ({ status: 'available', value: true }),
    });
    getRegistrationTokenMock.mockReset();
    resolveBcsEndpointMock.mockReset();
    resolveBcsEndpointMock.mockReturnValue('http://127.0.0.1:21000');
  });

  it('展示当前身份入口，并按 Bot 业务信息展示头像、名称、Bot 类型、引擎、运行状态', async () => {
    render(<WorkspaceIdentitySelector identities={identities} activeId="bot-online" onChange={() => {}} />);

    expect(screen.getByRole('button', { name: '当前协作身份：协作 Bot' })).toHaveClass('min-h-10');
    expect(screen.getByText('协作 Bot')).toBeInTheDocument();
    expect(screen.getByText('个人 Bot')).toBeInTheDocument();
    expect(screen.getByText('个人 Bot')).toHaveClass('rounded-sm', 'px-1', 'py-0', 'text-[10px]');
    expect(screen.getByText('OpenClaw')).toBeInTheDocument();
    expect(screen.getByText('运行状态：在线')).toBeInTheDocument();
    expect(screen.queryByText('Bot ID：')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '当前协作身份：协作 Bot' }));
    expect(
      screen.queryByText('当前协作身份决定在下方对话或群聊中，你以个人或指定 Bot 身份可查看的数据范围'),
    ).not.toBeInTheDocument();
    expect(await screen.findByText('隐藏 Bot')).toBeInTheDocument();
    expect(screen.getByText('ClaudeCode')).toBeInTheDocument();
    expect(screen.getByText('运行状态：不在线')).toBeInTheDocument();
  });

  it('将 bots 接口的引擎枚举统一为可读标签', () => {
    render(
      <WorkspaceIdentitySelector
        identities={[
          { id: 'openclaw', name: 'Openclaw Bot', kind: 'bot', avatar: 'O', engine: 'openclaw' },
          { id: 'claude-code', name: 'Claude Bot', kind: 'bot', avatar: 'C', engine: 'claude_code' },
          { id: 'hermes', name: 'Hermes Bot', kind: 'bot', avatar: 'H', engine: 'hermes' },
          { id: 'teclaw', name: 'TEClaw Bot', kind: 'bot', avatar: 'T', engine: 'teclaw' },
        ]}
        activeId="openclaw"
        onChange={() => {}}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: '当前协作身份：Openclaw Bot' }));

    expect(screen.getAllByText('OpenClaw')).toHaveLength(2);
    expect(screen.getByText('ClaudeCode')).toBeInTheDocument();
    expect(screen.getByText('Hermes')).toBeInTheDocument();
    expect(screen.getByText('TEClaw')).toBeInTheDocument();
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
        activeId="human_447147"
        userAvatarUrl="https://cdn.example.com/avatar.png"
        onChange={() => {}}
      />,
    );

    expect(screen.getByRole('img', { name: '风太' })).toHaveAttribute('src', 'https://cdn.example.com/avatar.png');
    expect(screen.getByText('用户')).toBeInTheDocument();
    expect(screen.getByText('工号：447147')).toBeInTheDocument();
    expect(screen.queryByText('OpenClaw')).not.toBeInTheDocument();
    expect(screen.queryByText('可参与群聊：')).not.toBeInTheDocument();
  });

  it('通过信息图标提供客观的数据范围说明，不使用观察者主体文案', async () => {
    render(<WorkspaceIdentitySelector identities={identities} activeId="human_447147" onChange={() => {}} />);

    const infoTrigger = screen.getByLabelText('协作身份说明');
    expect(infoTrigger).toBeInTheDocument();
    fireEvent.pointerMove(infoTrigger);

    expect(
      await screen.findByText('当前协作身份决定在下方对话或群聊中，你以个人或指定 Bot 身份可查看的数据范围'),
    ).toBeInTheDocument();
    expect(screen.queryByText(/我参与的会话|当前身份可见/)).not.toBeInTheDocument();
  });

  it('侧栏身份区不常显身份标签，说明收纳在下拉菜单的信息图标中', async () => {
    render(
      <WorkspaceIdentitySelector
        identities={identities}
        activeId="human_447147"
        onChange={() => {}}
        layout="sidebar"
      />,
    );

    expect(screen.queryByText('协作身份')).not.toBeInTheDocument();
    expect(screen.queryByText('可切换身份')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '当前协作身份：风太' }));
    const infoTrigger = await screen.findByLabelText('协作身份说明');
    fireEvent.pointerMove(infoTrigger);
    expect(
      await screen.findByText('当前协作身份决定在下方对话或群聊中，你以个人或指定 Bot 身份可查看的数据范围'),
    ).toBeInTheDocument();
  });

  it('只有一个协作身份时不显示切换提示', () => {
    render(<WorkspaceIdentitySelector identities={[identities[0]]} activeId="human_447147" onChange={() => {}} />);

    expect(screen.queryByText('可切换身份')).not.toBeInTheDocument();
  });

  it('不可达 Bot 仍仅展示运行状态，不展示群聊参与状态', () => {
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

    expect(screen.getByText('运行状态：在线')).toBeInTheDocument();
    expect(screen.queryByText(/可参与群聊/)).not.toBeInTheDocument();
    expect(screen.getByText('桌面 Bot')).toBeInTheDocument();
  });

  it('Bot 不在线时显示状态检查提示', async () => {
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

    expect(screen.getByText('运行状态：不在线')).toBeInTheDocument();
    expect(screen.queryByText(/可参与群聊/)).not.toBeInTheDocument();
    fireEvent.pointerMove(screen.getByLabelText('Bot 运行状态：不在线'));
    expect(await screen.findByText('请检查 Bot 实例状态')).toBeInTheDocument();
  });

  it('将低频协作权限入口放在身份下拉菜单标题行右侧并触发页面导航回调', async () => {
    const onOpenPermissions = jest.fn();
    render(
      <WorkspaceIdentitySelector
        identities={identities}
        activeId="human_447147"
        onChange={() => {}}
        onOpenPermissions={onOpenPermissions}
      />,
    );

    expect(screen.queryByRole('button', { name: '进入协作权限设置' })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '当前协作身份：风太' }));
    const permissionsButton = await screen.findByRole('button', { name: '进入协作权限设置' });
    expect(permissionsButton).toBeInTheDocument();
    expect(screen.getByText('切换协作身份')).toBeInTheDocument();
    fireEvent.click(permissionsButton);

    expect(onOpenPermissions).toHaveBeenCalledTimes(1);
    await waitFor(() => {
      expect(screen.queryByText('切换协作身份')).not.toBeInTheDocument();
    });
  });

  it('身份列表为空时不渲染身份下拉菜单或协作权限入口', () => {
    const onOpenPermissions = jest.fn();
    render(
      <WorkspaceIdentitySelector
        identities={[]}
        activeId={null}
        onChange={() => {}}
        onOpenPermissions={onOpenPermissions}
      />,
    );

    expect(screen.queryByRole('button', { name: '进入协作权限设置' })).not.toBeInTheDocument();
    expect(screen.queryByText('切换协作身份')).not.toBeInTheDocument();
    expect(onOpenPermissions).not.toHaveBeenCalled();
  });

  it('内部形态关闭接入新的 Bot 入口', async () => {
    extendCapabilities({
      getBotRegistrationEnabled: () => ({ status: 'available', value: false }),
    });
    render(<WorkspaceIdentitySelector identities={identities} activeId="human_447147" onChange={() => {}} />);

    fireEvent.click(screen.getByRole('button', { name: '当前协作身份：风太' }));
    await screen.findByText('隐藏 Bot');

    expect(screen.queryByRole('button', { name: '接入新的 Bot' })).not.toBeInTheDocument();
    expect(getRegistrationTokenMock).not.toHaveBeenCalled();
  });

  it('外部形态按需展示两种接入命令并复制真实 Token', async () => {
    const writeText = jest.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    getRegistrationTokenMock.mockResolvedValue({
      ok: true,
      data: {
        token: 'token-1',
        expiresAt: 1788272686000,
        note: 'Use this token for bot registration within 6 hours',
      },
    });
    render(<WorkspaceIdentitySelector identities={identities} activeId="human_447147" onChange={() => {}} />);

    fireEvent.click(screen.getByRole('button', { name: '当前协作身份：风太' }));
    expect(getRegistrationTokenMock).not.toHaveBeenCalled();
    fireEvent.click(await screen.findByRole('button', { name: '接入新的 Bot' }));

    await waitFor(() => expect(getRegistrationTokenMock).toHaveBeenCalledTimes(1));
    const dialog = await screen.findByRole('dialog');
    expect(dialog).toHaveTextContent('用户自助接入');
    expect(dialog).toHaveTextContent('install.sh --token token-1 --bcs-endpoint http://127.0.0.1:21000');
    expect(dialog).not.toHaveTextContent('Use this token for bot registration within 6 hours');
    expect(dialog).toHaveTextContent(/Token 有效期至：/);

    fireEvent.click(screen.getByRole('button', { name: 'Bot 自动接入' }));
    await waitFor(() => expect(screen.getByRole('dialog')).toHaveTextContent('your TOKEN is token-1'));
    fireEvent.click(screen.getByRole('button', { name: /复制/ }));

    await waitFor(() =>
      expect(writeText).toHaveBeenCalledWith(
        'Follow the instructions in https://raw.githubusercontent.com/inclusionAI/Avernet/refs/heads/dev/src/bcs/crates/plugins/openclaw-channel-bcn/install-instructions/install.md to join BCN (Bot Coordination Network), your TOKEN is token-1 and BCS_ENDPOINT is http://127.0.0.1:21000.',
      ),
    );
    expect(getRegistrationTokenMock).toHaveBeenCalledTimes(1);
  });

  it('打开入口和点击当前身份不会切换，点击其他身份后关闭 Popover', async () => {
    const onChange = jest.fn();
    render(<WorkspaceIdentitySelector identities={identities} activeId="human_447147" onChange={onChange} />);

    const trigger = screen.getByRole('button', { name: '当前协作身份：风太' });
    fireEvent.click(trigger);
    const currentOption = await screen.findByRole('button', { name: /风太 用户/ });
    fireEvent.click(currentOption);
    expect(onChange).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: /协作 Bot/ }));
    expect(onChange).toHaveBeenCalledWith('bot-online');
    await waitFor(() => {
      expect(screen.queryByText('切换协作身份')).not.toBeInTheDocument();
    });
  });
});
