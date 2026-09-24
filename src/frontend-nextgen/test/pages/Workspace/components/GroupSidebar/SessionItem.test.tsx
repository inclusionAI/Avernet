/** @jest-environment jsdom */
import type { IdentityView, ParticipantView, SessionView } from '@/domain/collaboration';
import { SessionItem } from '@/pages/Workspace/components/GroupSidebar/SessionItem';
import type { DomainResult } from '@/services/workspace/identityService';
import { describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';

// 本套件重度使用 Radix 组件（会话操作 DropdownMenu/分享弹窗等），jsdom 下异步渲染开销大（20s+/例，
// 串行实测约 19s/例），默认 30s 在并行/高负载下随机临界超时。统一放宽至 60s
// （仅本文件生效，不改全局 testTimeout，不改任何断言）。
jest.setTimeout(60000);

/** 群主（driver）视角身份。 */
const driverIdentity: IdentityView = { id: 'bot_driver', kind: 'user', displayName: '群主', online: true };

/** 普通成员视角身份（非 driver/manager/创建者）。 */
const memberIdentity: IdentityView = { id: 'human_member', kind: 'user', displayName: '普通成员', online: true };

const participants: ParticipantView[] = [
  { actorId: 'bot_driver', kind: 'bot', name: '驱动 Bot', role: 'driver', mode: 'auto' },
  { actorId: 'human_member', kind: 'human', name: '普通成员', role: 'member', mode: 'present' },
];

const session: SessionView = {
  sessionId: 's1',
  groupId: 'g1',
  title: '迭代排期会话',
  kind: 'chat',
  status: 'running',
  participants,
  lastMessageAt: 0,
  createdAt: 0,
  favorite: false,
};

function makeHandlers() {
  return {
    onSelectSession: jest.fn(),
    onToggleFavorite: jest.fn(),
    onManageSession: jest.fn(),
    onRenameSession: jest.fn(async () => true),
    onDeleteSession: jest.fn(async () => true),
    onShareSession: jest.fn(
      async () =>
        ({ ok: true as const, data: { invitationUrl: 'http://example.com/invite/s1' } } satisfies DomainResult<{
          invitationUrl: string;
        }>),
    ),
  };
}

function renderSessionItem(handlers = makeHandlers(), activeIdentity: IdentityView | null = driverIdentity) {
  return render(
    <SessionItem session={session} selected={false} favorite={false} activeIdentity={activeIdentity} {...handlers} />,
  );
}

function openMenu() {
  fireEvent.click(screen.getByRole('button', { name: '会话更多操作' }));
}

describe('SessionItem 会话操作菜单（验收微调：对齐管理面板与 Bot 单聊能力）', () => {
  it('管理者菜单四项齐备且顺序为 管理会话→编辑标题→分享会话→删除会话，删除项为危险色', () => {
    renderSessionItem();
    openMenu();

    const labels = ['管理会话', '编辑标题', '分享会话', '删除会话'];
    labels.forEach((label) => {
      expect(screen.getByRole('button', { name: label })).toBeInTheDocument();
    });
    // 顺序断言：菜单项在 DOM 中的先后顺序与约定一致。
    const menu = screen.getByRole('button', { name: '管理会话' }).closest('div');
    const buttons = Array.from(menu?.querySelectorAll('button') ?? []).map(
      (b) => b.getAttribute('aria-label') ?? b.textContent,
    );
    expect(buttons).toEqual(['管理会话', '编辑标题', '分享会话', '删除会话']);
    // 删除会话为危险色（对齐 Bot 单聊删除项）。
    expect(screen.getByRole('button', { name: '删除会话' })).toHaveClass('text-destructive');
  });

  it('编辑标题：打开弹窗回显当前标题，保存提交新标题', () => {
    const handlers = makeHandlers();
    renderSessionItem(handlers);
    openMenu();

    fireEvent.click(screen.getByRole('button', { name: '编辑标题' }));
    const input = screen.getByRole('textbox', { name: '会话标题' });
    expect((input as HTMLInputElement).value).toBe('迭代排期会话');
    fireEvent.change(input, { target: { value: '新会话标题' } });
    fireEvent.click(screen.getByRole('button', { name: '保存' }));
    expect(handlers.onRenameSession).toHaveBeenCalledWith('s1', '新会话标题');
  });

  it('编辑标题：空标题时保存禁用，不提交', () => {
    const handlers = makeHandlers();
    renderSessionItem(handlers);
    openMenu();

    fireEvent.click(screen.getByRole('button', { name: '编辑标题' }));
    const input = screen.getByRole('textbox', { name: '会话标题' });
    fireEvent.change(input, { target: { value: '   ' } });
    expect(screen.getByRole('button', { name: '保存' })).toBeDisabled();
    expect(handlers.onRenameSession).not.toHaveBeenCalled();
  });

  it('删除会话：二次确认后执行删除', () => {
    const handlers = makeHandlers();
    renderSessionItem(handlers);
    openMenu();

    fireEvent.click(screen.getByRole('button', { name: '删除会话' }));
    expect(screen.getByText('删除会话', { selector: '[role="alertdialog"] *' })).toBeInTheDocument();
    expect(within(screen.getByRole('alertdialog')).getByText(/迭代排期会话/)).toBeInTheDocument();
    fireEvent.click(within(screen.getByRole('alertdialog')).getByRole('button', { name: '确认删除' }));
    expect(handlers.onDeleteSession).toHaveBeenCalledWith('s1');
  });

  it('分享会话：打开分享弹窗并请求该会话的邀请链接', async () => {
    const handlers = makeHandlers();
    renderSessionItem(handlers);
    openMenu();

    fireEvent.click(screen.getByRole('button', { name: '分享会话' }));
    expect(handlers.onShareSession).toHaveBeenCalledWith('s1');
    // 链接生成是异步流程，等待 ShareDialog 回显。
    await waitFor(() => {
      expect(screen.getByDisplayValue('http://example.com/invite/s1')).toBeInTheDocument();
    });
  });

  it('普通成员：编辑标题/删除会话置灰禁用不可执行，管理/分享仍可用', () => {
    const handlers = makeHandlers();
    renderSessionItem(handlers, memberIdentity);
    openMenu();

    const renameButton = screen.getByRole('button', { name: '编辑标题' });
    const deleteButton = screen.getByRole('button', { name: '删除会话' });
    expect(renameButton).toHaveAttribute('aria-disabled', 'true');
    expect(deleteButton).toHaveAttribute('aria-disabled', 'true');
    expect(renameButton).toHaveClass('opacity-50', 'cursor-not-allowed');
    expect(deleteButton).toHaveClass('opacity-50', 'cursor-not-allowed');

    fireEvent.click(renameButton);
    fireEvent.click(deleteButton);
    expect(handlers.onRenameSession).not.toHaveBeenCalled();
    expect(handlers.onDeleteSession).not.toHaveBeenCalled();
    // 权限外的入口不打开任何弹窗（断言弹窗专属文案；Popover 菜单本身是 role=dialog 属预期）。
    expect(screen.queryByText('编辑会话标题')).not.toBeInTheDocument();
    expect(screen.queryByText('确认删除')).not.toBeInTheDocument();
    // 管理会话与分享会话不受权限影响。
    expect(screen.getByRole('button', { name: '管理会话' })).not.toHaveAttribute('aria-disabled');
    expect(screen.getByRole('button', { name: '分享会话' })).not.toHaveAttribute('aria-disabled');
  });

  it('会话创建者（非 driver 角色）同样可编辑与删除', () => {
    const creatorSession: SessionView = { ...session, createdBy: 'human_member' };
    const handlers = makeHandlers();
    render(
      <SessionItem
        session={creatorSession}
        selected={false}
        favorite={false}
        activeIdentity={memberIdentity}
        {...handlers}
      />,
    );
    openMenu();
    expect(screen.getByRole('button', { name: '编辑标题' })).not.toHaveAttribute('aria-disabled');
    expect(screen.getByRole('button', { name: '删除会话' })).not.toHaveAttribute('aria-disabled');
  });

  it('未提供操作回调时对应菜单项不渲染（列表降级）', () => {
    render(
      <SessionItem
        session={session}
        selected={false}
        favorite={false}
        onSelectSession={jest.fn()}
        onToggleFavorite={jest.fn()}
      />,
    );
    openMenu();
    expect(screen.queryByRole('button', { name: '管理会话' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '编辑标题' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '分享会话' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '删除会话' })).not.toBeInTheDocument();
  });
});
