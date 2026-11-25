/** @jest-environment jsdom */
// 会话行契约测试（restore-design-chat-page-b2-b3 Batch 1，dmore「会话目录二级*」帧 1:1）：
// 平齐化二级行、固定 14px 状态槽、无条数列；点击热区分级、hover/选中胶囊同长全宽同构、收藏星标槽位;
// 合并 PR 454 恢复的会话操作菜单（hover「…」:编辑标题/清除上下文/删除会话;收藏只走行内星标）。
import { ConversationSessionRow } from '@/pages/Workspace/Chat/components/ConversationSessionRow';
import type { ConversationSessionAction } from '@/services/workspace/conversationSessionActionService';
import '@testing-library/jest-dom';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
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
  it.each([false, true])('无树形 rail、无默认状态图标、无条数列，文字 13px 平齐排版（selected=%s）', (selected) => {
    const { container } = render(<ConversationSessionRow session={session} selected={selected} onSelect={jest.fn()} />);
    const row = container.firstElementChild as HTMLElement;
    // 平齐化裁决（B1 tasks 6.1 + 本批 spec）：不出现树形竖线/折线指示元素。
    expect(row.querySelector('[data-session-tree-rail]')).toBeNull();
    expect(row.querySelector('[data-session-tree-elbow]')).toBeNull();
    // 无有效状态时不展示兜底对话图标；标题仍由会话行固定缩进保持对齐。
    expect(container.querySelector('svg.lucide-message-square')).toBeNull();
    // 稿二级行右余部无「N 条」列。
    expect(screen.queryByText('21 条')).not.toBeInTheDocument();
    // 文字 13px/18px；未选中与一级名称同色,选中点亮 #09090B(2026-11-02 稿=text-foreground)但不加黑加粗。
    const title = screen.getByText('对话会话');
    expect(title).toHaveClass('text-[13px]', 'leading-[18px]');
    expect(title).toHaveClass('font-normal');
    expect(title).toHaveClass(selected ? 'text-foreground' : 'text-content-strong');
  });

  it('标题对齐一级名称：固定头像槽后起排；非收藏行右缘内缩，收藏行右缘到边', () => {
    const { container, rerender } = render(
      <ConversationSessionRow session={session} selected={false} onSelect={jest.fn()} />,
    );
    const plainButton = screen.getByRole('button', { name: '对话会话' });
    // 52px margin + 8px padding，与一级名称的 16px 外距 + 36px 头像槽 + 8px 间隔一致。
    expect(plainButton).toHaveClass('ml-[52px]', 'mr-4');
    // hover 背景由全宽胶囊承载（见下条同几何用例），选中不再使用行首品牌指示棒。
    expect(plainButton).not.toHaveClass('bg-foreground/[6%]');

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

  it('选中胶囊中性深：全宽 bg-foreground/[6%] rx6（2026-11-02 稿，选中底 #09090B0F）', () => {
    const { container } = render(<ConversationSessionRow session={session} selected onSelect={jest.fn()} />);
    const row = container.firstElementChild as HTMLElement;
    const capsule = row.querySelector('span[aria-hidden="true"]') as HTMLElement;
    // 几何契约不变(inset-x-2 全宽 rx6);选中底回归稿面 #09090B0F ≈ foreground 6%。
    expect(capsule).toHaveClass('left-2', 'right-2', 'rounded-md', 'bg-foreground/[6%]');
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

describe('ConversationSessionRow 固定右侧操作区（hover 不改变标题宽度）', () => {
  it('无常驻星标的非收藏交互行静态使用 pr-[80px]，hover 不再切换右内边距', () => {
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
    // 标题宽度保持不变：右侧一直保留完整操作区，不随 hover 切换右内边距。
    expect(button).toHaveClass('pl-2', 'pr-[80px]');
    expect(button).not.toHaveClass('pr-2');
    expect(button).not.toHaveClass('group-hover/row:pr-[80px]');
  });

  it('收藏或选中的常驻星标行预留时间槽，hover 不再切换右内边距', () => {
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
    expect(button).toHaveClass('pr-[104px]');
    expect(button).not.toHaveClass('pr-2');
    expect(button).not.toHaveClass('group-hover/row:pr-[104px]');

    // 选中且未收藏：星标同样常驻，因此同样需要时间槽，且右边界与收藏行对齐。
    rerender(<ConversationSessionRow session={session} selected onSelect={jest.fn()} onToggleFavorite={jest.fn()} />);
    expect(button).toHaveClass('pr-[88px]');
    expect(button).not.toHaveClass('pr-[80px]');
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

describe('ConversationSessionRow AgentCoding 长标题 Tooltip', () => {
  beforeEach(() => {
    Object.defineProperty(globalThis, 'ResizeObserver', {
      configurable: true,
      value: class ResizeObserverMock {
        observe() {}
        unobserve() {}
        disconnect() {}
      },
    });
    HTMLElement.prototype.hasPointerCapture = jest.fn(() => false);
    HTMLElement.prototype.setPointerCapture = jest.fn();
    HTMLElement.prototype.releasePointerCapture = jest.fn();
    HTMLElement.prototype.scrollIntoView = jest.fn();
  });

  it('标题真实溢出时，延迟悬浮展示完整标题', async () => {
    const longTitle = '执行一次非常长非常长非常长会被截断的默认 workflow';
    Object.defineProperty(HTMLSpanElement.prototype, 'clientWidth', {
      configurable: true,
      get: () => 60,
    });
    Object.defineProperty(HTMLSpanElement.prototype, 'scrollWidth', {
      configurable: true,
      get: () => 160,
    });
    try {
      render(
        <ConversationSessionRow
          session={{ ...session, title: longTitle }}
          selected={false}
          onSelect={jest.fn()}
          showFullTitleTooltip
        />,
      );
      const title = screen.getByText(longTitle);
      fireEvent.focus(title);
      await waitFor(() => {
        expect(screen.getAllByText(longTitle)).toHaveLength(2);
      });
      const tooltip = screen.getByRole('tooltip');
      expect(tooltip).toHaveTextContent(longTitle);
      expect(tooltip).toHaveClass(
        'max-w-[min(400px,calc(100vw-32px))]',
        'whitespace-pre-wrap',
        'break-words',
        '[overflow-wrap:anywhere]',
      );
    } finally {
      delete (HTMLSpanElement.prototype as { clientWidth?: number }).clientWidth;
      delete (HTMLSpanElement.prototype as { scrollWidth?: number }).scrollWidth;
    }
  });

  it('普通行不启用 AgentCoding 专属长标题 Tooltip', () => {
    const longTitle = '普通 Bot 不弹出完整标题';
    Object.defineProperty(HTMLSpanElement.prototype, 'clientWidth', {
      configurable: true,
      get: () => 30,
    });
    Object.defineProperty(HTMLSpanElement.prototype, 'scrollWidth', {
      configurable: true,
      get: () => 160,
    });
    try {
      render(<ConversationSessionRow session={{ ...session, title: longTitle }} selected={false} onSelect={jest.fn()} />);
      fireEvent.focus(screen.getByText(longTitle));
      expect(screen.getAllByText(longTitle)).toHaveLength(1);
      expect(screen.queryByRole('tooltip')).not.toBeInTheDocument();
    } finally {
      delete (HTMLSpanElement.prototype as { clientWidth?: number }).clientWidth;
      delete (HTMLSpanElement.prototype as { scrollWidth?: number }).scrollWidth;
    }
  });

  it('未发生真实溢出时不展示标题悬浮框', () => {
    const longTitle = '未截断的 Coding 会话标题';
    render(
      <ConversationSessionRow
        session={{ ...session, title: longTitle }}
        selected={false}
        onSelect={jest.fn()}
        showFullTitleTooltip
      />,
    );
    fireEvent.focus(screen.getByText(longTitle));
    expect(screen.getAllByText(longTitle)).toHaveLength(1);
    expect(screen.queryByRole('tooltip')).not.toBeInTheDocument();
  });
});

describe('ConversationSessionRow 相对时间（2026-10-22 体验精修）', () => {
  it('无星标时时间回到原右缘；hover/聚焦/触屏/菜单开启仅淡出让位图标', () => {
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
    // 固定 pr 只影响标题右界；时间从布局流移出，回到按钮原右缘独立淡出。
    expect(screen.getByRole('button', { name: '对话会话' })).not.toHaveClass(
      'group-hover/row:pr-[80px]',
      'group-hover/row:pr-[88px]',
    );
    expect(time).toHaveClass(
      'absolute',
      'right-2',
      'top-1/2',
      '-translate-y-1/2',
      'pointer-events-none',
      'transition-opacity',
      'duration-150',
      'ease-out',
      'group-hover/row:opacity-0',
      'group-focus-within/row:opacity-0',
      'group-has-[[data-state=open]]/row:opacity-0',
    );
    expect(time).not.toHaveClass('group-hover/row:max-w-0');
    expect(time).not.toHaveClass('group-hover/row:ml-0');
  });


  it('已收藏或选中的常驻星标与时间分层显示，不让时间压到星标右侧', () => {
    const gmtModified = new Date(Date.now() - 3 * 60 * 60 * 1000).toISOString();
    const { rerender } = render(
      <ConversationSessionRow
        session={{ ...session, favorite: true, gmtModified }}
        selected={false}
        onSelect={jest.fn()}
        onToggleFavorite={jest.fn()}
      />,
    );
    let time = screen.getByText('3 小时前');
    let starSlot = screen.getByRole('button', { name: '取消收藏' }).parentElement as HTMLElement;
    // 常驻星标行：时间位于星标左侧（right-[52px]），星标仍在原 right-7 槽位。
    expect(time).toHaveClass('absolute', 'right-[52px]');
    expect(starSlot).toHaveClass('absolute', 'right-7');

    rerender(
      <ConversationSessionRow
        session={{ ...session, gmtModified }}
        selected
        onSelect={jest.fn()}
        onToggleFavorite={jest.fn()}
      />,
    );
    time = screen.getByText('3 小时前');
    starSlot = screen.getByRole('button', { name: '收藏会话' }).parentElement as HTMLElement;
    // 非收藏但选中：星标也常驻；时间结合非收藏行 mr 锚到同一行内 60px 位置。
    expect(time).toHaveClass('absolute', 'right-[44px]');
    expect(starSlot).toHaveClass('absolute', 'right-7');
    expect(screen.getByRole('button', { name: '对话会话' })).toHaveClass('pr-[88px]');
  });

  it('Browse-only 行无操作入口时，hover 不改标题右界且时间常驻原位显示', () => {
    const gmtModified = new Date(Date.now() - 3 * 60 * 60 * 1000).toISOString();
    render(<ConversationSessionRow session={{ ...session, gmtModified }} selected={false} onSelect={jest.fn()} />);
    const button = screen.getByRole('button', { name: '对话会话' });
    // 无 actions / 收藏回调 => hover 没有右侧图标区，不应触发标题右内边距让位。
    expect(button).toHaveClass('pr-2');
    expect(button).not.toHaveClass('group-hover/row:pr-[80px]');
    expect(button).not.toHaveClass('group-hover/row:pr-[88px]');
    // 时间没有 hover 退场档，也不进入右侧绝对定位槽，位置和可见性保持不变。
    const time = screen.getByText('3 小时前');
    expect(time).not.toHaveClass('absolute', 'right-2');
    expect(time).not.toHaveClass('group-hover/row:max-w-0');
    expect(time).not.toHaveClass('group-focus-within/row:max-w-0');
  });

  it('gmtModified 缺失不渲染时间节点；只读行时间常驻不参与让位', () => {
    render(<ConversationSessionRow session={session} selected={false} onSelect={jest.fn()} />);
    expect(screen.queryByText('刚刚')).not.toBeInTheDocument();
    // 只读行右侧无图标区，时间常驻在普通布局流中，不参与 hover 让位。
    const gmtModified = new Date(Date.now() - 5 * 60 * 1000).toISOString();
    render(
      <ConversationSessionRow session={{ ...session, gmtModified }} selected={false} readOnly onSelect={jest.fn()} />,
    );
    const time = screen.getByText('5 分钟前');
    expect(time).not.toHaveClass('absolute');
    expect(time).not.toHaveClass('group-hover/row:max-w-0');
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
  it('hover「…」菜单不重复提供收藏,收藏只走行内星标', async () => {
    const user = userEvent.setup();
    const onToggleFavorite = jest.fn();
    const actions = createActions();
    render(
      <ConversationSessionRow
        session={{ ...session, favorite: true }}
        selected={false}
        onSelect={jest.fn()}
        onToggleFavorite={onToggleFavorite}
        actions={actions}
      />,
    );
    await user.click(screen.getByRole('button', { name: '会话更多操作' }));
    const menu = await screen.findByRole('dialog');
    const items = within(menu).getAllByRole('button');
    expect(items.map((item) => item.textContent)).toEqual(['编辑标题', '清除上下文', '删除会话']);
    // 外部星标是唯一收藏入口,避免菜单和行内操作重复。
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
    expect(await screen.findByText('删除会话', { selector: '[role="alertdialog"] *' })).toBeInTheDocument();
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

describe('ConversationSessionRow Bot 回复中指示（#2990 还原）', () => {
  it('replyingSessionId 命中当前行:渲染旋转指示(role=status),绝对定位不挤标题', () => {
    const { container } = render(
      <ConversationSessionRow session={session} selected onSelect={jest.fn()} replyingSessionId="session-1" />,
    );
    const status = screen.getByRole('status', { name: 'Bot 正在回复' });
    // 40-54px 缩进区,标题起点 60px 不随 loading 出现跳动。
    expect(status).toHaveClass('left-[40px]', 'top-1/2', 'z-10');
    const spinner = status.querySelector('svg');
    expect(spinner).toHaveClass('animate-spin');
    // 标题热区让位不受影响:标题起点仍为 52px margin。
    expect(screen.getByRole('button', { name: '对话会话' })).toHaveClass('ml-[52px]');
    expect(container.firstElementChild).not.toBeNull();
  });

  it('未命中行不渲染指示;只读行即使命中也不渲染', () => {
    const { unmount } = render(
      <ConversationSessionRow session={session} selected={false} onSelect={jest.fn()} replyingSessionId="other" />,
    );
    expect(screen.queryByRole('status', { name: 'Bot 正在回复' })).not.toBeInTheDocument();
    unmount();
    render(
      <ConversationSessionRow
        session={session}
        selected={false}
        readOnly
        onSelect={jest.fn()}
        replyingSessionId="session-1"
      />,
    );
    expect(screen.queryByRole('status', { name: 'Bot 正在回复' })).not.toBeInTheDocument();
  });
});
