/** @jest-environment jsdom */
// Bot 行折叠契约:收起角标是展开态 Bot 的鼠标折叠入口(转发同一 toggle),
// 主按钮仍是唯一的键盘/读屏控件(角标不进 Tab 序)。
import { ConversationBotItem } from '@/pages/Workspace/Chat/components/ConversationBotItem';
import type { ChatBotView } from '@/services/workspace/botSessionService';
import '@testing-library/jest-dom';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

const bot: ChatBotView = {
  botId: 'bot-1',
  realBotId: 'bot-1',
  displayName: '测试 Bot',
  online: true,
  chatable: true,
};

const noop = () => undefined;

function renderBotItem(
  overrides: {
    expanded?: boolean;
    onToggle?: () => void;
    bot?: Partial<ChatBotView>;
    onOpenBotManagement?: (bot: ChatBotView) => void;
  } = {},
) {
  const onToggle = overrides.onToggle ?? jest.fn();
  const utils = render(
    <ConversationBotItem
      bot={{ ...bot, ...overrides.bot }}
      section="managed"
      expanded={overrides.expanded ?? false}
      selectedSessionId={null}
      origin="mine"
      scope="all"
      sessionKey=""
      sessions={undefined}
      onToggle={onToggle}
      onOriginChange={noop}
      onScopeChange={noop}
      onSearchSession={noop}
      onCreateSession={noop}
      onSelectSession={noop}
      onLoadMore={noop}
      onToggleFavorite={noop}
      isFavoritePending={() => false}
      onOpenAgentCodingBot={noop}
      onOpenBotManagement={overrides.onOpenBotManagement}
    />,
  );
  return { ...utils, onToggle };
}

describe('ConversationBotItem 操作区', () => {
  it('遮盖底撤除:操作区不再叠 sidebar-actions-cover 底色,按钮间保持等宽 2px 间隙', () => {
    renderBotItem();
    const cover = screen.getByRole('button', { name: '新建会话' }).parentElement as HTMLElement;
    expect(cover).toBeTruthy();
    // 不再叠与行底不一致的操作区底色。
    expect(cover.className).not.toContain('sidebar-actions-cover-hover');
    expect(cover.className).not.toContain('sidebar-actions-cover-selected');
    // 间隙节奏统一:按钮间 gap-0.5(2px);right-2 贴行右缘(「更多操作」占位常居末位)。
    expect(cover.className).toContain('gap-0.5');
    expect(cover.className).toContain('right-2');
  });

  it('展开或筛选激活时文字让位:行内标题获得 122px 预留,图标不被文字压底', () => {
    const { container } = renderBotItem({ expanded: true });
    const title = Array.from(container.querySelectorAll('span')).find((el) =>
      el.className.includes('truncate'),
    ) as HTMLElement;
    expect(title?.parentElement).toHaveClass('pr-[122px]');
  });

  it('「更多操作」入口统一按菜单项渲染(2026-10-21 收紧):无菜单项不显示入口', () => {
    // 无 onOpenBotManagement 回调(好友 Bot 口径):入口不渲染。
    const first = renderBotItem();
    expect(screen.queryByRole('button', { name: '更多操作' })).not.toBeInTheDocument();
    first.unmount();
    // 有回调但空间信息缺失:入口同样不渲染(无空壳占位)。
    renderBotItem({ onOpenBotManagement: jest.fn() });
    expect(screen.queryByRole('button', { name: '更多操作' })).not.toBeInTheDocument();
  });
});

describe('ConversationBotItem 展开箭头', () => {
  it('收起的 Bot 行不渲染角标(2026-10-20 设计稿:仅展开的 Bot 显示收起角标)', () => {
    const { container } = renderBotItem();
    expect(container.querySelector('span[role="presentation"]')).toBeNull();
  });

  it('展开的 Bot 行渲染紧跟徽章的收起角标:点击角标触发 onToggle(不双触发)', () => {
    // 默认 fixture 无 engine 时徽章不渲染;传 engine 让「名称+徽章+角标」完整成形。
    const { container, onToggle } = renderBotItem({ expanded: true, bot: { engine: 'openclaw' } });
    const arrow = container.querySelector('span[role="presentation"]') as HTMLElement;
    expect(arrow).toBeTruthy();
    // 角标默认跟在名称/徽章行内(非行尾右对齐):与徽章同容器相邻。
    expect(arrow.previousElementSibling).toHaveClass('max-w-full');
    fireEvent.click(arrow);
    expect(onToggle).toHaveBeenCalledTimes(1);
  });

  it('角标不进 Tab 序(键盘折叠由主按钮承载),主按钮点击同样触发 onToggle', () => {
    const { onToggle } = renderBotItem({ expanded: true });
    const tabbables = Array.from(document.querySelectorAll('button')).filter(
      (el) => el.getAttribute('tabIndex') !== '-1',
    );
    // 主按钮唯一可聚焦;角标为 span(presentation)天然不可聚焦。
    expect(tabbables.map((el) => el.getAttribute('aria-label'))).toContain('测试 Bot');
    fireEvent(
      tabbables.find((el) => el.getAttribute('aria-label') === '测试 Bot') as HTMLElement,
      new MouseEvent('click', { bubbles: true }),
    );
    expect(onToggle).toHaveBeenCalledTimes(1);
  });

  it('不可chatable的 Bot 不渲染箭头', () => {
    const { container } = render(
      <ConversationBotItem
        bot={{ ...bot, chatable: false }}
        section="managed"
        expanded={false}
        selectedSessionId={null}
        origin="mine"
        scope="all"
        sessionKey=""
        onToggle={noop}
        onOriginChange={noop}
        onScopeChange={noop}
        onSearchSession={noop}
        onCreateSession={noop}
        onSelectSession={noop}
        onLoadMore={noop}
        onToggleFavorite={noop}
        isFavoritePending={() => false}
        onOpenAgentCodingBot={noop}
      />,
    );
    // 整行只剩主按钮,展开角标(span)也不渲染。
    expect(container.querySelectorAll('button').length).toBe(1);
    expect(container.querySelector('span[role="presentation"]')).toBeNull();
  });
});

describe('ConversationBotItem 外跳 Bot', () => {
  it.each([['managed'], ['team'], ['friend']] as const)('%s 分组 Coding Bot 可点且不出现普通会话操作区', (section) => {
    const onOpenAgentCodingBot = jest.fn();
    const { container, getByRole } = render(
      <ConversationBotItem
        bot={{
          ...bot,
          isAgentCodingBot: true,
          templateName: 'Coding Bot',
          chatable: false,
        }}
        section={section}
        expanded={false}
        selectedSessionId={null}
        origin="mine"
        scope="all"
        sessionKey=""
        onToggle={noop}
        onOriginChange={noop}
        onScopeChange={noop}
        onSearchSession={noop}
        onCreateSession={noop}
        onSelectSession={noop}
        onLoadMore={noop}
        onToggleFavorite={noop}
        isFavoritePending={() => false}
        onOpenAgentCodingBot={onOpenAgentCodingBot}
      />,
    );

    const trigger = getByRole('button', { name: '测试 Bot' });
    expect(trigger).not.toHaveClass('opacity-50', 'cursor-not-allowed');
    fireEvent.click(trigger);
    expect(onOpenAgentCodingBot).toHaveBeenCalled();
    expect(container.querySelector('button[aria-hidden="true"]')).toBeNull();
    expect(screen.queryByRole('button', { name: '会话ID检索' })).not.toBeInTheDocument();
  });
});

describe('ConversationBotItem 会话ID检索入口', () => {
  const renderRow = (over: {
    scope?: 'all' | 'favorite';
    origin?: 'mine' | 'others';
    sessionKey?: string;
    expanded?: boolean;
    chatable?: boolean;
    isAgentCodingBot?: boolean;
    onSearchSession?: (sessionKey: string) => void;
  } = {}) => {
    const onSearchSession = over.onSearchSession ?? jest.fn();
    const utils = render(
      <ConversationBotItem
        bot={{
          ...bot,
          chatable: over.chatable ?? true,
          isAgentCodingBot: over.isAgentCodingBot,
        }}
        section="managed"
        expanded={over.expanded ?? false}
        selectedSessionId={null}
        origin={over.origin ?? 'mine'}
        scope={over.scope ?? 'all'}
        sessionKey={over.sessionKey ?? ''}
        onToggle={noop}
        onOriginChange={noop}
        onScopeChange={noop}
        onSearchSession={onSearchSession}
        onCreateSession={noop}
        onSelectSession={noop}
        onLoadMore={noop}
        onToggleFavorite={noop}
        isFavoritePending={() => false}
        onOpenAgentCodingBot={noop}
      />,
    );
    return { ...utils, onSearchSession };
  };

  it('有效范围 all(含他人发起视角)始终提供搜索入口', () => {
    renderRow({ origin: 'mine', scope: 'all' });
    expect(screen.getByRole('button', { name: '会话ID检索' })).toBeInTheDocument();
  });

  it('仅 all 有效范围展示搜索切换到 favorite 后隐藏', () => {
    renderRow({ scope: 'favorite' });
    expect(screen.queryByRole('button', { name: '会话ID检索' })).not.toBeInTheDocument();
  });

  it('origin=others 沿用 all 有效范围,仍保留搜索入口', () => {
    renderRow({ origin: 'others' });
    expect(screen.getByRole('button', { name: '会话ID检索' })).toBeInTheDocument();
  });

  it('AgentCoding 与不可聊 Bot 不渲染搜索入口', () => {
    const first = renderRow({ isAgentCodingBot: true });
    expect(screen.queryByRole('button', { name: '会话ID检索' })).not.toBeInTheDocument();
    first.unmount();
    renderRow({ chatable: false });
    expect(screen.queryByRole('button', { name: '会话ID检索' })).not.toBeInTheDocument();
  });

  it('生效中的检索在收起时仍可见:操作区常驻并给行名称让位', () => {
    const { container } = renderRow({ sessionKey: 'agent:main:live', expanded: false });
    const title = Array.from(container.querySelectorAll('span')).find((el) =>
      el.className.includes('truncate'),
    ) as HTMLElement;
    // 生效中的检索钉住操作区,行名称让位 pr-[122px]。
    expect(title?.parentElement).toHaveClass('pr-[122px]');
    const actions = Array.from(container.querySelectorAll('div')).find((el) =>
      el.className.includes('right-2 z-10'),
    ) as HTMLElement;
    expect(actions.className).toContain('opacity-100');
    // 🔍 按钮激活态主色,标记生效条件。
    expect(screen.getByRole('button', { name: '会话ID检索' })).toHaveClass('text-primary');
  });

  it('提交检索透传 onSearchSession(已 trim),清空提交传播空串', () => {
    const { onSearchSession } = renderRow({ expanded: true });
    fireEvent.click(screen.getByRole('button', { name: '会话ID检索' }));
    const input = screen.getByRole('textbox', { name: '会话ID检索' }) as HTMLInputElement;
    fireEvent.change(input, { target: { value: ' agent:main:k1 ' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(onSearchSession).toHaveBeenCalledWith('agent:main:k1');
    fireEvent.change(input, { target: { value: '' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(onSearchSession).toHaveBeenCalledWith('');
  });
});

describe('ConversationBotItem Bot 可用状态(FAILED/PENDING 整行禁用)', () => {
  it('FAILED Bot:整行置灰禁用,点击不触发展开,会话入口/角标/展开区不渲染(无运维直达回调时「…」亦不渲染)', () => {
    const { onToggle } = renderBotItem({ bot: { status: 'FAILED' }, expanded: true });
    // 行容器:置灰 + aria-disabled。
    const row = document.querySelector('div[aria-disabled="true"]') as HTMLElement;
    expect(row).toBeTruthy();
    expect(row.className).toContain('opacity-60');
    // 点击主按钮不触发展开。
    fireEvent.click(screen.getByRole('button', { name: '测试 Bot' }));
    expect(onToggle).not.toHaveBeenCalled();
    // 会话入口(新建/检索/筛选)与收起角标不渲染;展开区即使 expanded 也不渲染;
    // 「…」入口需有 Bot 管理菜单项才渲染,本用例无回调故不出现。
    expect(screen.queryByRole('button', { name: '新建会话' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '更多操作' })).not.toBeInTheDocument();
    expect(document.querySelector('span[role="presentation"]')).toBeNull();
    expect(screen.queryByLabelText('会话列表：测试 Bot')).not.toBeInTheDocument();
  });

  it('不可用 Bot 运维直达(2026-10-21):「…」入口常驻,菜单仅「Bot 管理」,会话类入口不渲染', () => {
    const onOpenBotManagement = jest.fn();
    const { container } = renderBotItem({
      bot: { status: 'FAILED', spaceId: '27' },
      onOpenBotManagement,
    });
    // 「…」入口常驻显示(不依赖 hover;父容器 opacity-100)。
    const moreButton = screen.getByRole('button', { name: '更多操作' });
    expect(moreButton.parentElement).toHaveClass('opacity-100');
    // 会话类入口不渲染(不可用 Bot 的会话操作无意义)。
    expect(screen.queryByRole('button', { name: '新建会话' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '会话ID检索' })).not.toBeInTheDocument();
    // 收起角标仍不渲染。
    expect(container.querySelector('span[role="presentation"]')).toBeNull();
    // 菜单含「Bot 管理」,点击触发跳转回调。
    fireEvent.click(moreButton);
    fireEvent.click(screen.getByRole('button', { name: /Bot 管理/ }));
    expect(onOpenBotManagement).toHaveBeenCalledTimes(1);
  });

  it('PENDING Bot:同样整行禁用,点击不触发展开', () => {
    const { onToggle } = renderBotItem({ bot: { status: 'PENDING' } });
    fireEvent.click(screen.getByRole('button', { name: '测试 Bot' }));
    expect(onToggle).not.toHaveBeenCalled();
    expect(document.querySelector('div[aria-disabled="true"]')).toBeTruthy();
  });

  it('不可用 Bot 悬停显示原因 tooltip(FAILED 与 PENDING 文案区分)', async () => {
    const { unmount } = renderBotItem({ bot: { status: 'FAILED' } });
    const failedRow = document.querySelector('div[aria-disabled="true"]') as HTMLElement;
    fireEvent.pointerMove(failedRow, { pointerType: 'mouse' });
    fireEvent.focusIn(failedRow);
    await waitFor(() =>
      expect(
        screen.getByText('该 Bot 因创建或运行异常暂时无法使用；可在 Bot 管理中尝试运维恢复'),
      ).toBeInTheDocument(),
    );
    unmount();

    renderBotItem({ bot: { status: 'PENDING' } });
    const pendingRow = document.querySelector('div[aria-disabled="true"]') as HTMLElement;
    fireEvent.pointerMove(pendingRow, { pointerType: 'mouse' });
    fireEvent.focusIn(pendingRow);
    await waitFor(() =>
      expect(screen.getByText('该 Bot 正在创建或重启中，暂时无法使用，完成后可刷新重试')).toBeInTheDocument(),
    );
  });

  it('ACTIVE Bot 行为不变:可展开,无禁用属性', () => {
    const { onToggle } = renderBotItem({ bot: { status: 'ACTIVE' } });
    fireEvent.click(screen.getByRole('button', { name: '测试 Bot' }));
    expect(onToggle).toHaveBeenCalledTimes(1);
    expect(document.querySelector('div[aria-disabled="true"]')).toBeNull();
  });

  it('缺省 status(好友关系等无状态数据源)行为不变:可展开', () => {
    const { onToggle } = renderBotItem();
    fireEvent.click(screen.getByRole('button', { name: '测试 Bot' }));
    expect(onToggle).toHaveBeenCalledTimes(1);
  });
});

describe('ConversationBotItem 「Bot 管理」菜单项', () => {
  it('我的/团队 Bot 且接口返回空间信息:菜单显示「Bot 管理」,点击触发跳转回调并关闭菜单', () => {
    const onOpenBotManagement = jest.fn();
    renderBotItem({
      bot: { spaceId: '27', spaceName: '个人空间' },
      onOpenBotManagement,
    });
    fireEvent.click(screen.getByRole('button', { name: '更多操作' }));
    fireEvent.click(screen.getByRole('button', { name: /Bot 管理/ }));
    expect(onOpenBotManagement).toHaveBeenCalledTimes(1);
    // 回调收到带空间信息的 bot 视图。
    const invoked = onOpenBotManagement.mock.calls[0][0] as ChatBotView;
    expect(invoked.spaceId).toBe('27');
    // 菜单项点击后菜单关闭(菜单项移除;筛选器的常驻 Popper wrapper 不作判定依据)。
    expect(screen.queryByRole('button', { name: /Bot 管理/ })).not.toBeInTheDocument();
  });

  it('好友 Bot(未提供回调)不渲染「Bot 管理」菜单项', () => {
    renderBotItem();
    // 统一收紧(2026-10-21):无菜单项不放空壳入口,「…」整体不渲染。
    expect(screen.queryByRole('button', { name: '更多操作' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Bot 管理/ })).not.toBeInTheDocument();
  });

  it('空间信息缺失(接口未返回 space)不渲染「Bot 管理」菜单项', () => {
    renderBotItem({ onOpenBotManagement: jest.fn() });
    // 空间信息缺失:菜单项条件不满足,入口同样不渲染(无空壳)。
    expect(screen.queryByRole('button', { name: '更多操作' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Bot 管理/ })).not.toBeInTheDocument();
  });
});

describe('ConversationBotItem 行徽标', () => {
  const renderBadgeRow = (overrides: Partial<ChatBotView> = {}) => {
    render(
      <ConversationBotItem
        bot={{ ...bot, ...overrides }}
        section="managed"
        expanded={false}
        selectedSessionId={null}
        origin="mine"
        scope="all"
        sessionKey=""
        onToggle={noop}
        onOriginChange={noop}
        onScopeChange={noop}
        onSearchSession={noop}
        onCreateSession={noop}
        onSelectSession={noop}
        onLoadMore={noop}
        onToggleFavorite={noop}
        isFavoritePending={() => false}
        onOpenAgentCodingBot={noop}
      />,
    );
  };

  it('AgentCoding 缺模板名但保留模板分类时展示分类而不是 AgentCoding', () => {
    renderBadgeRow({ isAgentCodingBot: true, chatable: false, templateType: 'generalCC' });
    expect(screen.getByText('generalCC')).toBeInTheDocument();
    expect(screen.queryByText('AgentCoding')).not.toBeInTheDocument();
  });

  it('普通 CC Bot 展示引擎标签而不是 AgentCoding', () => {
    renderBadgeRow({ engine: 'claude_code' });
    expect(screen.getByText('ClaudeCode')).toBeInTheDocument();
    expect(screen.queryByText('AgentCoding')).not.toBeInTheDocument();
  });

  it('徽章与名称同行跟随,超长模板名按徽章宽度截断且不新增 Tooltip', () => {
    renderBadgeRow({
      isAgentCodingBot: true,
      chatable: false,
      templateName: '非常长非常长非常长非常长模板名',
    });
    const label = screen.getByText('非常长非常长非常长非常长模板名');
    // 2026-10-20 设计稿还原:徽章与名称同一行(父容器横向排列、垂直居中),不再位于名称下方独立行。
    expect(label.parentElement).toHaveClass('items-center');
    expect(label.parentElement).not.toHaveClass('flex-col');
    expect(label).toHaveClass('max-w-full', 'truncate');
    expect(label.closest('[data-state="tooltip"]')).toBeNull();
  });

  it.each([
    ['openclaw', 'OpenClaw'],
    ['teclaw', 'TEClaw'],
    ['claude_code', 'ClaudeCode'],
  ] as const)('引擎 %s 徽章统一中性描边样式（%s），不再按引擎分色', (engine, label) => {
    renderBadgeRow({ engine });
    const badge = screen.getByText(label);
    // 2026-10-20 设计稿还原：透明底 + --border(#E4E4E7) 边框 + 深灰字，引擎分色仅保留给 Bot 管理页。
    expect(badge).toHaveClass('border-border', 'text-muted-foreground');
    expect(badge.className).not.toMatch(/text-(primary|success|purple)/);
  });

  it('AgentCoding 模板徽章保持模板名标签，样式同样取统一中性描边', () => {
    renderBadgeRow({ isAgentCodingBot: true, chatable: false, engine: 'claude_code', templateName: 'Coding Bot' });
    // 标签口径不变（模板名优先），颜色不再按引擎取色。
    const badge = screen.getByText('Coding Bot');
    expect(badge).toHaveClass('border-border', 'text-muted-foreground');
    expect(badge.className).not.toMatch(/text-purple/);
  });

  it('未知引擎徽章回退显示引擎原文，样式统一中性', () => {
    renderBadgeRow({ engine: 'unknown_engine' });
    expect(screen.getByText('unknown_engine')).toHaveClass('border-border', 'text-muted-foreground');
  });
});
