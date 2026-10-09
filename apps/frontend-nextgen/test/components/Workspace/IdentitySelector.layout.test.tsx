/** @jest-environment jsdom */
/**
 * IdentitySelector 测试拆分（layout 分册）：布局形态与 hover 提示类用例。
 *
 * 拆分背景与口径见 IdentitySelector.display.test.tsx 头注。
 * 用例与断言逐字保留，仅做文件级拆分（零断言语义变化）。
 */
import { extendCapabilities } from '@/capabilities';
import { WorkspaceIdentitySelector } from '@/components/Workspace/IdentitySelector';
import { botRegistrationService, resolveBcsEndpoint } from '@/services/workspace/botRegistrationService';
import type { Identity } from '@/services/workspace/workspaceModel';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

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

describe('WorkspaceIdentitySelector 布局形态与 hover 提示', () => {
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
    expect(screen.getByText('在线')).toBeInTheDocument();
    expect(screen.queryByText('Bot ID：')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '当前协作身份：协作 Bot' }));
    expect(
      screen.queryByText(
        '当前工作身份决定你以个人或指定 Bot 身份使用工作区各项功能，并影响各菜单中可查看的数据和可执行的操作。',
      ),
    ).not.toBeInTheDocument();
    expect(await screen.findByText('隐藏 Bot')).toBeInTheDocument();
    expect(screen.getByText('ClaudeCode')).toBeInTheDocument();
    expect(screen.getByText('离线')).toBeInTheDocument();
  });

  it('collaboration 布局只由切换按钮作为 Popover trigger，且切换按钮可打开/关闭列表', async () => {
    render(
      <WorkspaceIdentitySelector
        identities={identities}
        activeId="human_900004"
        onChange={() => {}}
        layout="collaboration"
      />,
    );

    expect(screen.getByText('当前协作身份').parentElement).toHaveClass('text-xs');
    expect(screen.queryByLabelText('协作身份说明')).not.toBeInTheDocument();
    const switchButton = screen.getByRole('button', { name: '切换工作身份' });
    const identityCard = screen.getByRole('button', { name: '当前协作身份：示例用户' });
    expect(switchButton).toHaveAttribute('aria-haspopup', 'dialog');
    expect(switchButton).toHaveAttribute('aria-expanded', 'false');
    expect(
      screen.getAllByRole('button').filter((button) => button.getAttribute('aria-haspopup') === 'dialog'),
    ).toHaveLength(1);
    expect(screen.getByText(/用户 ID：900004/)).toBeInTheDocument();
    expect(identityCard).not.toHaveClass('min-h-[76px]');
    expect(identityCard).toHaveClass(
      'h-auto',
      'min-h-10',
      'rounded-lg',
      'border',
      'bg-muted/40',
      'px-4',
      'py-2',
      'gap-2',
      'w-full',
    );
    expect(screen.getByLabelText('示例用户')).toHaveStyle({ width: '24px', height: '24px' });
    expect(screen.getByText('示例用户')).toHaveClass('text-xs');
    expect(screen.getByText(/用户 ID：900004/)).toHaveClass('text-xs');
    expect(identityCard.querySelector('svg')).toHaveClass('h-3', 'w-3');

    fireEvent.click(switchButton);
    expect(switchButton).toHaveAttribute('aria-expanded', 'true');
    const popoverContent = await screen.findByRole('dialog');
    expect(popoverContent).toHaveClass('w-[var(--radix-popover-trigger-width)]');
    expect(popoverContent).not.toHaveClass('w-[320px]');
    const collaborationOption = await screen.findByRole('button', { name: /协作 Bot/ });
    expect(collaborationOption).toBeInTheDocument();
    expect(screen.getByLabelText('协作 Bot')).toHaveStyle({ width: '32px', height: '32px' });
    expect(screen.getByText('协作 Bot')).toHaveClass('text-xs');

    fireEvent.keyDown(document, { key: 'Escape' });
    await waitFor(() => expect(screen.queryByText('隐藏 Bot')).not.toBeInTheDocument());
    expect(switchButton).toHaveAttribute('aria-expanded', 'false');

    fireEvent.click(switchButton);
    expect(switchButton).toHaveAttribute('aria-expanded', 'true');
    fireEvent.click(switchButton);
    await waitFor(() => expect(screen.queryByText('隐藏 Bot')).not.toBeInTheDocument());
    expect(switchButton).toHaveAttribute('aria-expanded', 'false');
  });

  it('collaboration 自定义标题的说明紧跟标题，切换入口保留在右侧', async () => {
    render(
      <WorkspaceIdentitySelector
        identities={identities}
        activeId="bot-online"
        onChange={() => {}}
        layout="collaboration"
        headerLabel="为 Ta 加好友："
        headerTooltip="每个身份都拥有独立的好友关系。"
      />,
    );

    const title = screen.getByText('为 Ta 加好友：');
    const description = screen.getByLabelText('协作身份说明');
    const switchButton = screen.getByRole('button', { name: '切换工作身份' });
    expect(title.parentElement).toContainElement(description);
    expect(title.parentElement).toContainElement(switchButton.parentElement);
    fireEvent.pointerMove(description);
    expect(await screen.findByText('每个身份都拥有独立的好友关系。')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '当前协作身份：协作 Bot' })).toBeInTheDocument();
  });

  it('collaboration 身份卡鼠标打开和关闭后仍保留卡片焦点', async () => {
    const user = userEvent.setup();
    render(
      <WorkspaceIdentitySelector
        identities={identities}
        activeId="human_900004"
        onChange={() => {}}
        layout="collaboration"
      />,
    );

    const identityCard = screen.getByRole('button', { name: '当前协作身份：示例用户' });
    await user.click(identityCard);

    expect(identityCard).toHaveAttribute('aria-expanded', 'true');
    expect(document.activeElement).toBe(identityCard);

    await user.click(identityCard);

    await waitFor(() => {
      expect(identityCard).toHaveAttribute('aria-expanded', 'false');
      expect(document.activeElement).toBe(identityCard);
    });
  });

  it('collaboration 身份卡支持键盘打开并由 Escape 关闭', async () => {
    const user = userEvent.setup();
    render(
      <WorkspaceIdentitySelector
        identities={identities}
        activeId="human_900004"
        onChange={() => {}}
        layout="collaboration"
      />,
    );

    const identityCard = screen.getByRole('button', { name: '当前协作身份：示例用户' });
    identityCard.focus();
    await user.keyboard('{Enter}');

    expect(identityCard).toHaveAttribute('aria-expanded', 'true');
    expect(await screen.findByRole('button', { name: /协作 Bot/ })).toBeInTheDocument();

    await user.keyboard('{Escape}');
    await waitFor(() => {
      expect(identityCard).toHaveAttribute('aria-expanded', 'false');
      expect(document.activeElement).toBe(identityCard);
    });

    await user.keyboard(' ');

    expect(identityCard).toHaveAttribute('aria-expanded', 'true');
    expect(await screen.findByRole('button', { name: /协作 Bot/ })).toBeInTheDocument();

    await user.keyboard('{Escape}');
    await waitFor(() => {
      expect(identityCard).toHaveAttribute('aria-expanded', 'false');
      expect(document.activeElement).toBe(identityCard);
    });
  });

  it('collaboration 身份卡从切换按钮打开后由 Escape 关闭并回焦切换按钮', async () => {
    const user = userEvent.setup();
    render(
      <WorkspaceIdentitySelector
        identities={identities}
        activeId="human_900004"
        onChange={() => {}}
        layout="collaboration"
      />,
    );

    const switchButton = screen.getByRole('button', { name: '切换工作身份' });
    await user.click(switchButton);
    expect(await screen.findByRole('button', { name: /协作 Bot/ })).toBeInTheDocument();

    await user.keyboard('{Escape}');

    await waitFor(() => {
      expect(switchButton).toHaveAttribute('aria-expanded', 'false');
      expect(document.activeElement).toBe(switchButton);
    });
  });

  it('collaboration 身份卡只响应主指针按钮', () => {
    render(
      <WorkspaceIdentitySelector
        identities={identities}
        activeId="human_900004"
        onChange={() => {}}
        layout="collaboration"
      />,
    );

    const identityCard = screen.getByRole('button', { name: '当前协作身份：示例用户' });
    fireEvent.pointerDown(identityCard, { button: 2 });
    fireEvent.click(identityCard, { button: 2 });

    expect(identityCard).toHaveAttribute('aria-expanded', 'false');

    fireEvent.pointerDown(identityCard, { button: 0 });
    fireEvent.click(identityCard, { button: 0 });

    expect(identityCard).toHaveAttribute('aria-expanded', 'true');
  });

  it('collaboration 身份卡可手动打开/关闭列表，并选择 Bot 身份', async () => {
    const onChange = jest.fn();
    const user = userEvent.setup();
    render(
      <WorkspaceIdentitySelector
        identities={identities}
        activeId="human_900004"
        onChange={onChange}
        layout="collaboration"
      />,
    );

    const identityCard = screen.getByRole('button', { name: '当前协作身份：示例用户' });
    await user.click(identityCard);
    expect(await screen.findByRole('button', { name: /协作 Bot/ })).toBeInTheDocument();
    expect(identityCard).toHaveAttribute('aria-expanded', 'true');

    await user.click(identityCard);
    await waitFor(() => expect(screen.queryByText('隐藏 Bot')).not.toBeInTheDocument());
    expect(identityCard).toHaveAttribute('aria-expanded', 'false');

    await user.click(identityCard);
    await user.click(screen.getByRole('button', { name: /协作 Bot/ }));
    expect(onChange).toHaveBeenCalledWith('bot-online');
    await waitFor(() => {
      expect(screen.queryByText('隐藏 Bot')).not.toBeInTheDocument();
      expect(document.activeElement).toBe(identityCard);
    });
  });

  it('collaboration 身份卡从页面外点击关闭列表', async () => {
    const user = userEvent.setup();
    render(
      <div>
        <WorkspaceIdentitySelector
          identities={identities}
          activeId="human_900004"
          onChange={() => {}}
          layout="collaboration"
        />
        <div data-testid="outside">页面外</div>
      </div>,
    );

    const identityCard = screen.getByRole('button', { name: '当前协作身份：示例用户' });
    await user.click(identityCard);
    expect(await screen.findByRole('button', { name: /协作 Bot/ })).toBeInTheDocument();

    await user.click(screen.getByTestId('outside'));

    await waitFor(() => {
      expect(screen.queryByText('隐藏 Bot')).not.toBeInTheDocument();
      expect(document.activeElement).toBe(identityCard);
    });
  });

  it('headerLabel 定制时替换默认头部标签，且不显示「可切换其他协作身份」副提示', () => {
    render(
      <WorkspaceIdentitySelector
        identities={identities}
        activeId="bot-online"
        onChange={() => {}}
        headerLabel="为 Ta 加好友："
      />,
    );

    expect(screen.getByText('为 Ta 加好友：')).toBeInTheDocument();
    expect(screen.queryByText('当前协作身份')).not.toBeInTheDocument();
    expect(screen.queryByText('可切换其他协作身份')).not.toBeInTheDocument();
  });

  it('sidebar 布局下 headerLabel 定制同样替换「工作身份」标题', () => {
    render(
      <WorkspaceIdentitySelector
        identities={identities}
        activeId="bot-online"
        onChange={() => {}}
        layout="sidebar"
        headerLabel="为 Ta 加好友："
      />,
    );

    expect(screen.getByText('为 Ta 加好友：')).toBeInTheDocument();
    expect(screen.queryByText('工作身份')).not.toBeInTheDocument();
  });

  it('未定制 headerLabel 时保留默认头部标签与切换副提示', () => {
    render(<WorkspaceIdentitySelector identities={identities} activeId="bot-online" onChange={() => {}} />);

    expect(screen.getByText('当前协作身份')).toBeInTheDocument();
    expect(screen.getByText('可切换其他协作身份')).toBeInTheDocument();
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

  it('通过信息图标提供客观的数据范围说明，不使用观察者主体文案', async () => {
    render(<WorkspaceIdentitySelector identities={identities} activeId="human_900004" onChange={() => {}} />);

    const infoTrigger = screen.getByLabelText('协作身份说明');
    expect(infoTrigger).toBeInTheDocument();
    fireEvent.pointerMove(infoTrigger);

    expect(
      await screen.findByText(
        '当前工作身份决定你以个人或指定 Bot 身份使用工作区各项功能，并影响各菜单中可查看的数据和可执行的操作。',
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/我参与的会话|当前身份可见/)).not.toBeInTheDocument();
  });

  it('侧栏身份区突出头像、名称和身份类型，并明确影响工作下全部页面', async () => {
    render(
      <WorkspaceIdentitySelector identities={identities} activeId="bot-online" onChange={() => {}} layout="sidebar" />,
    );

    expect(screen.getByText('工作身份')).toBeInTheDocument();
    expect(screen.getByLabelText('工作身份说明')).toHaveClass('top-px', 'text-muted-foreground/70');
    expect(screen.getByLabelText('工作身份说明').querySelector('svg')).toHaveClass('h-3', 'w-3');
    expect(
      screen.queryByText(
        '当前工作身份决定你以个人或指定 Bot 身份使用工作区各项功能，并影响各菜单中可查看的数据和可执行的操作。',
      ),
    ).not.toBeInTheDocument();
    fireEvent.pointerMove(screen.getByLabelText('工作身份说明'));
    expect(
      await screen.findByText(
        '当前工作身份决定你以个人或指定 Bot 身份使用工作区各项功能，并影响各菜单中可查看的数据和可执行的操作。',
      ),
    ).toBeInTheDocument();
    expect(screen.getByLabelText('协作 Bot')).toHaveStyle({ width: '24px', height: '24px' });
    expect(screen.getByText('协作 Bot')).toHaveClass('text-xs');
    expect(screen.queryByText('个人 Bot')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '当前协作身份：协作 Bot' })).toHaveClass(
      'rounded-lg',
      'border',
      'bg-muted/40',
      'min-h-9',
      'px-2.5',
      'py-1.5',
    );
    expect(screen.queryByText('OpenClaw')).not.toBeInTheDocument();
    expect(screen.queryByText('在线')).not.toBeInTheDocument();
    expect(screen.queryByText('切换身份')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '当前协作身份：协作 Bot' }));
    expect(await screen.findByText('个人 Bot')).toBeInTheDocument();
    expect(screen.queryByLabelText('协作身份说明')).not.toBeInTheDocument();
  });

  it('折叠态为 Bot 使用较小的身份首字圆形入口，切换身份后关闭 hover 提示', async () => {
    const onChange = jest.fn();
    const view = render(
      <WorkspaceIdentitySelector
        identities={identities}
        activeId="bot-online"
        onChange={onChange}
        layout="collapsed"
      />,
    );

    expect(screen.queryByText('工作身份')).not.toBeInTheDocument();
    expect(screen.queryByText('协作 Bot')).not.toBeInTheDocument();
    expect(screen.queryByText('个人 Bot')).not.toBeInTheDocument();

    const trigger = screen.getByRole('button', { name: '当前协作身份：协作 Bot' });
    expect(trigger).toHaveTextContent('协');
    expect(trigger).toHaveClass('h-8', 'w-8', 'rounded-full');

    fireEvent.pointerMove(trigger);
    expect(await screen.findByRole('tooltip')).toHaveTextContent('协作 Bot');
    fireEvent.click(trigger);
    const hiddenBotOption = await screen.findByRole('button', { name: /隐藏 Bot/ });
    fireEvent.click(hiddenBotOption);
    view.rerender(
      <WorkspaceIdentitySelector
        identities={identities}
        activeId="bot-hidden"
        onChange={onChange}
        layout="collapsed"
      />,
    );

    expect(onChange).toHaveBeenCalledWith('bot-hidden');
    await waitFor(() => expect(screen.queryByRole('tooltip')).not.toBeInTheDocument());

    const switchedTrigger = screen.getByRole('button', { name: '当前协作身份：隐藏 Bot' });
    fireEvent.pointerMove(switchedTrigger);
    await expect(screen.findByRole('tooltip', {}, { timeout: 1000 })).rejects.toThrow();

    fireEvent.pointerLeave(switchedTrigger);
    const reenteredTrigger = screen.getByRole('button', { name: '当前协作身份：隐藏 Bot' });
    fireEvent.pointerMove(reenteredTrigger);
    expect(await screen.findByRole('tooltip')).toHaveTextContent('隐藏 Bot');
    fireEvent.pointerLeave(reenteredTrigger);
    await waitFor(() => expect(screen.queryByRole('tooltip')).not.toBeInTheDocument());
  });

  it('折叠态为人类身份展示用户头像，hover 时提示角色名称', async () => {
    render(
      <WorkspaceIdentitySelector
        identities={identities}
        activeId="human_900004"
        userAvatarUrl="https://cdn.example.com/avatar.png"
        onChange={() => {}}
        layout="collapsed"
      />,
    );

    const trigger = screen.getByRole('button', { name: '当前协作身份：示例用户' });
    expect(trigger).toHaveClass('h-8', 'w-8', 'rounded-full');
    expect(screen.getByRole('img', { name: '示例用户' })).toHaveAttribute('src', 'https://cdn.example.com/avatar.png');
    expect(trigger).not.toHaveTextContent('示');

    fireEvent.pointerMove(trigger);
    expect(await screen.findByRole('tooltip')).toHaveTextContent('示例用户');
  });
});
