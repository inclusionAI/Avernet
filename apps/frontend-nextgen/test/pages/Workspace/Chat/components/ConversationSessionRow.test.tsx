/** @jest-environment jsdom */
// 会话行契约测试（restore-design-chat-page-b2-b3 Batch 1，dmore「会话目录二级*」帧 1:1）：
// 平齐化（无 rail/前置图标/条数列）、hover 热区分级、选中胶囊全宽同构、收藏星标槽位;
// 合并 PR 454 恢复的会话操作菜单（hover「…」:编辑标题/清除上下文/删除会话,收藏不入菜单走星标）。
import { ConversationSessionRow } from '@/pages/Workspace/Chat/components/ConversationSessionRow';
import type { ConversationSessionAction } from '@/services/workspace/conversationSessionActionService';
import '@testing-library/jest-dom';
import { fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

const session = {
  sessionId: 'session-1',
  botId: 'bot-1',
  title: '对话会话',
  messageCount: 21,
  gmtCreate: '',
  gmtModified: '',
};

const createActions = () => ({
  run: jest.fn<Promise<boolean>, [ConversationSessionAction]>().mockResolvedValue(true),
  pending: false,
});

describe('ConversationSessionRow 平齐化与热区分级（dmore 1:1）', () => {
  it.each([false, true])('无树形 rail/前置消息图标、无条数列，文字 13px 平齐排版（selected=%s）', (selected) => {
    const { container } = render(<ConversationSessionRow session={session} selected={selected} onSelect={jest.fn()} />);
    const row = container.firstElementChild as HTMLElement;
    // 平齐化裁决（B1 tasks 6.1 + 本批 spec）：不出现树形竖线/折线指示元素。
    expect(row.querySelector('[data-session-tree-rail]')).toBeNull();
    expect(row.querySelector('[data-session-tree-elbow]')).toBeNull();
    // 稿二级行:无前置消息图标、右余部无「N 条」列。
    expect(container.querySelector('svg.lucide-message-square')).toBeNull();
    expect(screen.queryByText('21 条')).not.toBeInTheDocument();
    // 文字 13px/18px:正常 #525252(text-content-strong),选中 #09090B 中黑 500。
    const title = screen.getByText('对话会话');
    expect(title).toHaveClass('text-[13px]', 'leading-[18px]');
    // 条期望展开写（禁条件 expect）：选中 500/#09090B，未选 400/#525252。
    const expectation = selected ? ['font-medium', 'text-foreground'] : ['font-normal', 'text-content-strong'];
    expect(title).toHaveClass(...expectation);
  });

  it('hover 交互热区分级：非收藏行左缘内缩 24px、右缘内缩 8px；收藏行右缘到边', () => {
    const { container, rerender } = render(
      <ConversationSessionRow session={session} selected={false} onSelect={jest.fn()} />,
    );
    const plainButton = screen.getByRole('button', { name: '对话会话' });
    // 非收藏热区：ml-8（较 bot 胶囊 inset-x-2 再内缩 24px）/ mr-4（再内缩 8px，稿 Button 271px 宽）。
    expect(plainButton).toHaveClass('ml-8', 'mr-4');
    // hover 反馈交给行内胶囊（muted 弱化），选中不再使用行首品牌指示棒。
    expect(plainButton).not.toHaveClass('bg-primary/10');

    rerender(
      <ConversationSessionRow
        session={{ ...session, favorite: true }}
        selected={false}
        onSelect={jest.fn()}
        onToggleFavorite={jest.fn()}
      />,
    );
    const favoriteButton = screen.getByRole('button', { name: '对话会话' });
    // 收藏行热区右缘到边（稿 Button 279px 宽），为常驻星标让出完整热区。
    expect(favoriteButton).toHaveClass('ml-8', 'mr-2');
    expect(container.firstElementChild).not.toBeNull();
  });

  it('选中胶囊与 bot 行同构：全宽 bg-selected rx6（稿实拍 #EBEBEB x208..510）', () => {
    const { container } = render(<ConversationSessionRow session={session} selected onSelect={jest.fn()} />);
    const row = container.firstElementChild as HTMLElement;
    const capsule = row.querySelector('span[aria-hidden="true"]') as HTMLElement;
    expect(capsule).toHaveClass('left-2', 'right-2', 'rounded-md', 'bg-selected');
  });

  it('选中交互保持：整行可点、aria-current=page', () => {
    const onSelect = jest.fn();
    render(<ConversationSessionRow session={session} selected onSelect={onSelect} />);
    const button = screen.getByRole('button', { name: '对话会话' });
    expect(button).toHaveAttribute('aria-current', 'page');
    fireEvent.click(button);
    expect(onSelect).toHaveBeenCalledTimes(1);
  });

  it('只读行保留「只读」Badge，替代条数位', () => {
    render(
      <ConversationSessionRow
        session={session}
        selected={false}
        readOnly
        onSelect={jest.fn()}
        actions={createActions()}
      />,
    );
    expect(screen.getByRole('button', { name: '对话会话 只读' })).toBeInTheDocument();
    expect(screen.queryByText('21 条')).not.toBeInTheDocument();
    // 只读行无写入口:不出「会话更多操作」菜单。
    expect(screen.queryByRole('button', { name: '会话更多操作' })).not.toBeInTheDocument();
  });
});

describe('ConversationSessionRow 收藏动作', () => {
  it('独立的收藏动作不触发行选中，未收藏星标悬停显现', () => {
    const onSelect = jest.fn();
    const onToggleFavorite = jest.fn();
    render(
      <ConversationSessionRow
        session={{ ...session, favorite: false }}
        selected={false}
        onSelect={onSelect}
        onToggleFavorite={onToggleFavorite}
      />,
    );
    const star = screen.getByRole('button', { name: '收藏会话' });
    expect(star).toHaveClass('opacity-0', 'group-hover/row:opacity-100', 'group-focus-within/row:opacity-100');
    // 星标右缘槽位固定（稿 x267..283 → right-7），不随热区分级漂移。
    expect(star).toHaveClass('absolute', 'right-7');
    fireEvent.click(star);
    expect(onToggleFavorite).toHaveBeenCalledTimes(1);
    expect(onSelect).not.toHaveBeenCalled();
  });

  it('已收藏星标常驻、pending 期间禁用，填色走 --star', () => {
    render(
      <ConversationSessionRow
        session={{ ...session, favorite: true }}
        selected
        onSelect={jest.fn()}
        onToggleFavorite={jest.fn()}
        favoritePending
      />,
    );
    const star = screen.getByRole('button', { name: '取消收藏' });
    expect(star).toBeDisabled();
    expect(star).not.toHaveClass('opacity-0');
    expect(star.querySelector('svg')).toHaveClass('fill-star', 'text-star');
  });

  it('只读行不渲染收藏动作（即使传入回调）', () => {
    render(
      <ConversationSessionRow
        session={session}
        selected={false}
        readOnly
        onSelect={jest.fn()}
        onToggleFavorite={jest.fn()}
      />,
    );
    expect(screen.queryByRole('button', { name: '收藏会话' })).not.toBeInTheDocument();
  });

  it('收藏态未知时禁用而非猜测反向动作', () => {
    render(
      <ConversationSessionRow session={session} selected={false} onSelect={jest.fn()} onToggleFavorite={jest.fn()} />,
    );
    expect(screen.getByRole('button', { name: '收藏会话' })).toBeDisabled();
  });
});

describe('ConversationSessionRow 恢复的会话操作菜单（PR 454,平齐形态）', () => {
  it('hover「…」菜单承载编辑标题/清除上下文/删除会话,收藏不入菜单（星标常驻）', async () => {
    const user = userEvent.setup();
    const actions = createActions();
    render(
      <ConversationSessionRow
        session={{ ...session, favorite: true }}
        selected={false}
        onSelect={jest.fn()}
        onToggleFavorite={jest.fn()}
        actions={actions}
      />,
    );
    await user.click(screen.getByRole('button', { name: '会话更多操作' }));
    const menu = await screen.findByRole('dialog');
    const items = within(menu).getAllByRole('button');
    expect(items.map((item) => item.textContent)).toEqual(['编辑标题', '清除上下文', '删除会话']);
    // 收藏走行内常驻星标,菜单不重复提供翻转入口。
    expect(screen.getByRole('button', { name: '取消收藏' })).toBeInTheDocument();
    expect(within(menu).queryByText('取消收藏')).toBeNull();
    expect(within(menu).queryByText('收藏会话')).toBeNull();
  });

  it('菜单动作不触发行选中;删除确认后走 actions.run 以移除会话', async () => {
    const user = userEvent.setup();
    const onSelect = jest.fn();
    const actions = createActions();
    render(
      <ConversationSessionRow
        session={session}
        selected={false}
        onSelect={onSelect}
        onToggleFavorite={jest.fn()}
        actions={actions}
      />,
    );
    await user.click(screen.getByRole('button', { name: '会话更多操作' }));
    const menu = await screen.findByRole('dialog');
    await user.click(within(menu).getByRole('button', { name: /删除会话/ }));
    expect(await screen.findByText('删除会话', { selector : '[role="alertdialog"] *' })).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '确认删除' }));
    expect(actions.run).toHaveBeenCalledTimes(1);
    expect(actions.run.mock.calls[0][0]).toEqual({ type: 'delete' });
    expect(onSelect).not.toHaveBeenCalled();
  });

  it('菜单 hover 显现,菜单打开期间鼠标离行仍钉住可见', async () => {
    const user = userEvent.setup();
    const { container } = render(
      <ConversationSessionRow session={session} selected={false} onSelect={jest.fn()} actions={createActions()} />,
    );
    const slot = container.querySelector('[data-session-meta-actions]') as HTMLElement;
    expect(slot).toHaveClass('opacity-0', 'group-hover/row:opacity-100', 'group-focus-within/row:opacity-100');
    await user.click(screen.getByRole('button', { name: '会话更多操作' }));
    expect(slot).toHaveClass('opacity-100');
  });

  it('actions 进行中禁用菜单按钮,防重入', () => {
    render(
      <ConversationSessionRow
        session={session}
        selected={false}
        onSelect={jest.fn()}
        actions={{ run: jest.fn(async () => true), pending: true }}
      />,
    );
    // 读屏可访问名固定为「会话更多操作」(ariaLabel 覆盖),busy 只反映为禁用。
    expect(screen.getByRole('button', { name: '会话更多操作' })).toBeDisabled();
  });

  it('未传 actions 的行不渲染菜单入口（降级/未接入场景）', () => {
    render(<ConversationSessionRow session={session} selected={false} onSelect={jest.fn()} />);
    expect(screen.queryByRole('button', { name: '会话更多操作' })).not.toBeInTheDocument();
  });
});