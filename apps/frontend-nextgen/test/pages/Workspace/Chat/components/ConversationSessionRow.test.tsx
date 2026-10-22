/** @jest-environment jsdom */
// 会话行契约测试（restore-design-chat-page-b2-b3 Batch 1，dmore「会话目录二级*」帧 1:1）：
// 平齐化（无 rail/前置图标/条数列）、点击热区分级、hover/选中胶囊同长全宽同构、收藏星标槽位;
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
    // 稿二级行:无前置消息图标、右余部无「N 条」列。
    expect(container.querySelector('svg.lucide-message-square')).toBeNull();
    expect(screen.queryByText('21 条')).not.toBeInTheDocument();
    // 文字 13px/18px；未选中与一级名称同色,选中点亮品牌蓝(2026-10-22 二轮)但不加黑加粗。
    const title = screen.getByText('对话会话');
    expect(title).toHaveClass('text-[13px]', 'leading-[18px]');
    expect(title).toHaveClass('font-normal');
    expect(title).toHaveClass(selected ? 'text-primary' : 'text-content-strong');
  });

  it('标题对齐一级名称：固定头像槽后起排；非收藏行右缘内缩，收藏行右缘到边', () => {
    const { container, rerender } = render(
      <ConversationSessionRow session={session} selected={false} onSelect={jest.fn()} />,
    );
    const plainButton = screen.getByRole('button', { name: '对话会话' });
    // 52px margin + 8px padding，与一级名称的 16px 外距 + 36px 头像槽 + 8px 间隔一致。
    expect(plainButton).toHaveClass('ml-[52px]', 'mr-4');
    // hover 背景由全宽胶囊承载（见下条同几何用例），选中不再使用行首品牌指示棒。
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
    expect(favoriteButton).toHaveClass('ml-[52px]', 'mr-2');
    expect(container.firstElementChild).not.toBeNull();
  });

  it('选中胶囊升级品牌蓝：全宽 bg-primary/10 rx6（2026-10-22 二轮，告别与 hover 灰底不可辨）', () => {
    const { container } = render(<ConversationSessionRow session={session} selected onSelect={jest.fn()} />);
    const row = container.firstElementChild as HTMLElement;
    const capsule = row.querySelector('span[aria-hidden="true"]') as HTMLElement;
    // 几何契约不变(inset-x-2 全宽 rx6);选 中改壳 shell 主导航同语言的品牌蓝底。
    expect(capsule).toHaveClass('left-2', 'right-2', 'rounded-md', 'bg-primary/10');
  });

  it('hover 胶囊与选中胶囊同几何：同样 inset-x-2 全宽，背景不随热区分级变短', () => {
    const { container } = render(<ConversationSessionRow session={session} selected={false} onSelect={jest.fn()} />);
    const row = container.firstElementChild as HTMLElement;
    const hoverPill = row.querySelector('span[aria-hidden="true"]') as HTMLElement;
    // 与选中胶囊同一 left-2/right-2/rounded-md 几何；弱化 muted/60 底，hover/focus/触屏显现。
    expect(hoverPill).toHaveClass('left-2', 'right-2', 'rounded-md', 'bg-muted/60');
    expect(hoverPill).toHaveClass('opacity-0', 'group-hover/row:opacity-100', 'group-focus-within/row:opacity-100');
    // 按钮自身不再画 hover 背景，避免双层背景/长度差。
    const button = screen.getByRole('button', { name: '对话会话' });
    expect(button).toHaveClass('hover:bg-transparent');
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

describe('ConversationSessionRow 长标题让位（文字不与 hover 图标互叠）', () => {
  it('静默态保持基础 pr-2；hover/聚焦/触屏/菜单开启让至「…」左界（右界 96px）', () => {
    render(
      <ConversationSessionRow
        session={session}
        selected={false}
        onSelect={jest.fn()}
        onToggleFavorite={jest.fn()}
        actions={createActions()}
      />,
    );
    const button = screen.getByRole('button', { name: '对话会话' });
    // 静默态图标全隐：右内边距维持基础 8px（文字右界 = mr-4 + pr-2 = 24px）。
    expect(button).toHaveClass('pl-2', 'pr-2');
    // 让位通道与图标自身显现条件同构；菜单开启期由 trigger data-state=open 承接（鼠标离行仍钉住可见）。
    expect(button).toHaveClass(
      'group-hover/row:pr-[80px]',
      'group-focus-within/row:pr-[80px]',
      '[@media(hover:none)]:pr-[80px]',
      'group-has-[[data-state=open]]/row:pr-[80px]',
    );
  });

  it('收藏行星标常驻 → 常驻让位 pr-[52px]（右界 60px）；hover 期连「…」让足 pr-[88px]', () => {
    const { rerender } = render(
      <ConversationSessionRow
        session={{ ...session, favorite: true }}
        selected={false}
        onSelect={jest.fn()}
        onToggleFavorite={jest.fn()}
        actions={createActions()}
      />,
    );
    const button = screen.getByRole('button', { name: '对话会话' });
    // 常驻让位替换基础 pr-2（twMerge 取后者）；hover 档覆盖非收藏档 pr-80。
    expect(button).toHaveClass('pr-[52px]');
    expect(button).not.toHaveClass('pr-2');
    expect(button).toHaveClass('group-hover/row:pr-[88px]');
    expect(button).not.toHaveClass('group-hover/row:pr-[80px]');

    // 选中且未收藏：星标同样常驻 → 常驻让位 pr-[44px]（mr-4 → 右界同为 60px）。
    rerender(
      <ConversationSessionRow session={session} selected onSelect={jest.fn()} onToggleFavorite={jest.fn()} />,
    );
    expect(button).toHaveClass('pr-[44px]');
    expect(button).not.toHaveClass('pr-2');
  });

  it('只读行无图标 → 不让位，保持基础内边距', () => {
    render(<ConversationSessionRow session={session} selected={false} readOnly onSelect={jest.fn()} />);
    const button = screen.getByRole('button', { name: '对话会话 只读' });
    // pr-2 存活即证明无任何让位档生效（twMerge 会移除被覆盖的基础值）。
    expect(button).toHaveClass('pl-2', 'pr-2');
    expect(button).not.toHaveClass('group-hover/row:pr-[80px]');
  });
});

describe('ConversationSessionRow 相对时间（2026-10-22 体验精修）', () => {
  it('静默态右缘轻提示相对时间（gmtModified），hover/聚焦/触屏/菜单开启让位图标', () => {
    const gmtModified = new Date(Date.now() - 3 * 60 * 60 * 1000).toISOString();
    render(
      <ConversationSessionRow
        session={{ ...session, gmtModified }}
        selected={false}
        onSelect={jest.fn()}
        onToggleFavorite={jest.fn()}
      />,
    );
    // 3 小时前的相对时间口径 + 11px 弱化 hint 色 + 静默可见。
    const time = screen.getByText('3 小时前');
    expect(time).toHaveClass('text-[11px]', 'text-content-hint');
    // 让位触发集与星标显隐同构（方向相反）：图标出现时时间退场。
    expect(time).toHaveClass(
      'group-hover/row:opacity-0',
      'group-focus-within/row:opacity-0',
      'group-has-[[data-state=open]]/row:opacity-0',
    );
  });

  it('gmtModified 缺失不渲染时间节点；只读行时间常驻不参与让位', () => {
    render(<ConversationSessionRow session={session} selected={false} onSelect={jest.fn()} />);
    expect(screen.queryByText('刚刚')).not.toBeInTheDocument();
    // 只读行右侧无图标区，时间常驻（无 opacity-0 让位档）。
    const gmtModified = new Date(Date.now() - 5 * 60 * 1000).toISOString();
    render(
      <ConversationSessionRow session={{ ...session, gmtModified }} selected={false} readOnly onSelect={jest.fn()} />,
    );
    const time = screen.getByText('5 分钟前');
    expect(time).not.toHaveClass('group-hover/row:opacity-0');
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
    // 显隐/定位挂外层包装 span：Button 基类 disabled:opacity-50 特异性更高，
    // 加载更多在途（全列表星标 disabled）会顶穿按钮级 opacity-0 → 未 hover 全量显影。
    const starSlot = star.parentElement as HTMLElement;
    expect(starSlot).toHaveClass('opacity-0', 'group-hover/row:opacity-100', 'group-focus-within/row:opacity-100');
    // 星标右缘槽位固定（稿 x267..283 → right-7），不随热区分级漂移。
    expect(starSlot).toHaveClass('absolute', 'right-7');
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
    // 常驻 = 外层包装 span 无隐藏档（button 自身不再承运显隐）。
    expect(star.parentElement).not.toHaveClass('opacity-0');
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
