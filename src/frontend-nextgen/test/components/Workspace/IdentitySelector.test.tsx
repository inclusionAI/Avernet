/** @jest-environment jsdom */
import { WorkspaceIdentitySelector } from '@/components/Workspace/IdentitySelector';
import type { Identity } from '@/services/workspace/workspaceModel';
import { describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

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
  it('展示当前身份入口，并按 Bot 业务信息展示头像、名称、Bot 类型、引擎和可群聊状态', async () => {
    render(<WorkspaceIdentitySelector identities={identities} activeId="bot-online" onChange={() => {}} />);

    expect(screen.getByRole('button', { name: '当前协作身份：协作 Bot' })).toHaveClass('min-h-10');
    expect(screen.getByText('协作 Bot')).toBeInTheDocument();
    expect(screen.getByText('个人 Bot')).toBeInTheDocument();
    expect(screen.getByText('OpenClaw')).toBeInTheDocument();
    expect(screen.getByText('可群聊')).toBeInTheDocument();
    expect(screen.queryByText('Bot ID：')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '当前协作身份：协作 Bot' }));
    expect(await screen.findByText('隐藏 Bot')).toBeInTheDocument();
    expect(screen.getByText('ClaudeCode')).toBeInTheDocument();
    expect(screen.getByText('不可群聊')).toBeInTheDocument();
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
    expect(screen.queryByText('可群聊')).not.toBeInTheDocument();
  });

  it('在当前协作身份旁提供简短说明提示', async () => {
    render(<WorkspaceIdentitySelector identities={identities} activeId="human_447147" onChange={() => {}} />);

    const infoTrigger = screen.getByLabelText('协作身份说明');
    expect(infoTrigger).toBeInTheDocument();
    fireEvent.pointerMove(infoTrigger);

    expect(
      await screen.findByText('当前协作身份决定在下方对话或群聊中，你以个人或指定 Bot 身份可查看的数据范围'),
    ).toBeInTheDocument();
  });

  it('沿用既有状态语义：群聊文案由 chatStatus 决定，不因可达性变化而改写', () => {
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

    expect(screen.getByText('可群聊')).toBeInTheDocument();
    expect(screen.getByText('桌面 Bot')).toBeInTheDocument();
    expect(screen.queryByText('不可群聊')).not.toBeInTheDocument();
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
