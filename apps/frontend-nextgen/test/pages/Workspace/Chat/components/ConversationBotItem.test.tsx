/** @jest-environment jsdom */
// Bot 行折叠契约:展开箭头是鼠标可点的折叠入口(转发同一 toggle),
// 主按钮仍是唯一的键盘/读屏控件(箭头不进 Tab 序)。
import { ConversationBotItem } from '@/pages/Workspace/Chat/components/ConversationBotItem';
import type { ChatBotView } from '@/services/workspace/botSessionService';
import '@testing-library/jest-dom';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { ReactElement } from 'react';

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
    botSelected?: boolean;
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
      botSelected={overrides.botSelected ?? false}
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

function rerenderBotItem(
  rerender: (ui: ReactElement) => void,
  overrides: { expanded?: boolean; botSelected?: boolean } = {},
) {
  rerender(
    <ConversationBotItem
      bot={bot}
      section="managed"
      expanded={overrides.expanded ?? false}
      selectedSessionId={null}
      botSelected={overrides.botSelected ?? false}
      origin="mine"
      scope="all"
      sessionKey=""
      sessions={undefined}
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
}

describe('ConversationBotItem 归属增强（2026-10-22 体验精修二轮）', () => {
  it('展开箭头旋转过渡:收起 -rotate-90,展开归零(替代 Right↔Down 硬切换)', () => {
    const { container } = renderBotItem({ expanded: false });
    const arrowSvg = (container.querySelector('button[aria-hidden="true"]') as HTMLElement).querySelector('svg');
    expect(arrowSvg).toHaveClass('-rotate-90');
  });

  it('展开区带 conversation-area-in 进入动效类(reduced-motion 由 global.css 静止)', () => {
    const { container } = renderBotItem({ expanded: true });
    const area = container.querySelector('div[aria-label^="会话列表："]');
    expect(area).toHaveClass('conversation-area-in');
  });
});

describe('ConversationBotItem 操作区', () => {
  it('遮盖底撤除:操作区不再叠 sidebar-actions-cover 底色,筛选/加号/行尾箭头保持等宽 2px 间隙', () => {
    renderBotItem();
    const cover = screen.getByRole('button', { name: '新建会话' }).parentElement as HTMLElement;
    expect(cover).toBeTruthy();
    // 不再叠与行底不一致的操作区底色。
    expect(cover.className).not.toContain('sidebar-actions-cover-hover');
    expect(cover.className).not.toContain('sidebar-actions-cover-selected');
    // 间隙节奏统一:按钮间 gap-0.5(2px),行尾为箭头按钮(w-6 + px-2)让出同宽的 2px。
    expect(cover.className).toContain('gap-0.5');
    expect(cover.className).toContain('right-[34px]');
  });

  it('展开或筛选激活时文字让位:行内标题获得 pr-24 预留,图标不被文字压底', () => {
    const { container } = renderBotItem({ expanded: true });
    const title = Array.from(container.querySelectorAll('span')).find((el) =>
      el.className.includes('truncate'),
    ) as HTMLElement;
    expect(title?.parentElement).toHaveClass('pr-24');
  });

  it('有「更多操作」入口时让位扩至 pr-[122px](4 按钮几何,#2990 还原)', () => {
    const { container } = renderBotItem({
      expanded: true,
      bot: { spaceId: '101' },
      onOpenBotManagement: noop,
    });
    const title = Array.from(container.querySelectorAll('span')).find((el) =>
      el.className.includes('truncate'),
    ) as HTMLElement;
    expect(title?.parentElement).toHaveClass('pr-[122px]');
    // 非钉住态(收起且无筛选/检索)走临时让位变体,菜单打开期间(data-state=open)同样让位,
    // 鼠标移出行、菜单仍开时不塌缩。
    const { container: collapsed } = renderBotItem({
      bot: { spaceId: '101' },
      onOpenBotManagement: noop,
    });
    const collapsedTitle = Array.from(collapsed.querySelectorAll('span')).find((el) =>
      el.className.includes('truncate'),
    ) as HTMLElement;
    const collapsedColumn = collapsedTitle.parentElement as HTMLElement;
    expect(collapsedColumn.className).toContain('group-hover:pr-[122px]');
    expect(collapsedColumn.className).toContain('group-has-[[data-state=open]]:pr-[122px]');
  });

  it('当前舞台所属 Bot 名称点亮品牌蓝(行锚点,与会话胶囊两级选中)', () => {
    const { container, rerender } = renderBotItem({ botSelected: true });
    const title = Array.from(container.querySelectorAll('span')).find((el) =>
      el.className.includes('truncate'),
    ) as HTMLElement;
    expect(title).toHaveClass('text-primary');
    // 名称规格与会话标题一致(2026-10-22 需求):13px 常规字重,目录不再加粗强调。
    expect(title).toHaveClass('text-[13px]', 'font-normal');
    // 非舞台 Bot 保持常规灰名。
    rerenderBotItem(rerender, { botSelected: false });
    const plainTitle = Array.from(container.querySelectorAll('span')).find((el) =>
      el.className.includes('truncate'),
    ) as HTMLElement;
    expect(plainTitle).not.toHaveClass('text-primary');
  });
});

describe('ConversationBotItem 展开箭头', () => {
  it('箭头指向同一 toggle:点击箭头触发 onToggle', () => {
    const { onToggle } = renderBotItem();
    const arrow = document.querySelector('button[aria-hidden="true"]') as HTMLElement;
    expect(arrow).toHaveAttribute('tabIndex', '-1');
    fireEvent.click(arrow);
    expect(onToggle).toHaveBeenCalledTimes(1);
  });

  it('箭头不进 Tab 序(键盘折叠由主按钮承载),主按钮点击同样触发 onToggle', () => {
    const { onToggle } = renderBotItem({ expanded: true });
    const tabbables = Array.from(document.querySelectorAll('button')).filter(
      (el) => el.getAttribute('tabIndex') !== '-1',
    );
    // 主按钮唯一可聚焦;箭头按钮 tabIndex=-1 不算。
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
    // 整行只剩主按钮,无展开箭头按钮。
    expect(container.querySelectorAll('button').length).toBe(1);
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
    // 生效中的检索钉住操作区,行名称让位 pr-24。
    expect(title?.parentElement).toHaveClass('pr-24');
    const actions = Array.from(container.querySelectorAll('div')).find((el) =>
      el.className.includes('right-[34px]'),
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
  it('FAILED Bot:整行置灰禁用,点击不触发展开,操作区/箭头/展开区不渲染', () => {
    const { onToggle } = renderBotItem({ bot: { status: 'FAILED' }, expanded: true });
    // 行容器:置灰 + aria-disabled。
    const row = document.querySelector('div[aria-disabled="true"]') as HTMLElement;
    expect(row).toBeTruthy();
    expect(row.className).toContain('opacity-60');
    // 点击主按钮不触发展开。
    fireEvent.click(screen.getByRole('button', { name: '测试 Bot' }));
    expect(onToggle).not.toHaveBeenCalled();
    // 操作区(新建/检索/筛选)与展开箭头不渲染;展开区即使 expanded 也不渲染。
    expect(screen.queryByRole('button', { name: '新建会话' })).not.toBeInTheDocument();
    expect(document.querySelector('button[aria-hidden="true"]')).toBeNull();
    expect(screen.queryByLabelText('会话列表：测试 Bot')).not.toBeInTheDocument();
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

describe('ConversationBotItem 「更多操作」菜单（#2990 还原）', () => {
  it('提供回调且 Bot 有 spaceId:渲染「更多操作」入口', () => {
    renderBotItem({ bot: { spaceId: '101' }, onOpenBotManagement: noop });
    expect(screen.getByRole('button', { name: '更多操作' })).toBeInTheDocument();
  });

  it('未传回调或缺 spaceId 不渲染入口(有菜单项才显示,#2990 收紧口径)', () => {
    const { unmount } = renderBotItem({ bot: { spaceId: '101' } });
    expect(screen.queryByRole('button', { name: '更多操作' })).not.toBeInTheDocument();
    unmount();
    renderBotItem({ onOpenBotManagement: noop });
    expect(screen.queryByRole('button', { name: '更多操作' })).not.toBeInTheDocument();
  });

  it('点击「Bot 管理」触发 onOpenBotManagement(bot) 并关闭菜单', async () => {
    const onOpenBotManagement = jest.fn();
    renderBotItem({ bot: { spaceId: '101' }, onOpenBotManagement });
    fireEvent.click(screen.getByRole('button', { name: '更多操作' }));
    fireEvent.click(screen.getByRole('button', { name: /Bot 管理/ }));
    await waitFor(() => expect(onOpenBotManagement).toHaveBeenCalledTimes(1));
    expect(onOpenBotManagement.mock.calls[0][0]).toMatchObject({ botId: 'bot-1', spaceId: '101' });
    // 菜单项点击后关闭。
    expect(screen.queryByRole('button', { name: /Bot 管理/ })).not.toBeInTheDocument();
  });

  it('FAILED 行无回调不渲染「…」(不可用行不出现空菜单)', () => {
    renderBotItem({ bot: { status: 'FAILED', spaceId: '101' } });
    expect(screen.queryByRole('button', { name: '更多操作' })).not.toBeInTheDocument();
  });

  it('不可用 Bot「…」常驻可见且菜单仅「Bot 管理」(运维直达,会话类入口不渲染)', async () => {
    const onOpenBotManagement = jest.fn();
    const { container } = renderBotItem({
      bot: { status: 'FAILED', spaceId: '101' },
      onOpenBotManagement,
    });
    const actions = Array.from(container.querySelectorAll('div')).find((el) =>
      el.className.includes('right-[34px]'),
    ) as HTMLElement;
    // 常驻可见;不随 hover 变化。
    expect(actions.className).toContain('opacity-100');
    // 会话类入口不渲染。
    expect(screen.queryByRole('button', { name: '新建会话' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '会话ID检索' })).not.toBeInTheDocument();
    // 菜单内容仅「Bot 管理」一项。
    fireEvent.click(screen.getByRole('button', { name: '更多操作' }));
    expect(screen.getByRole('button', { name: /Bot 管理/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Bot 管理/ }));
    await waitFor(() => expect(onOpenBotManagement).toHaveBeenCalledTimes(1));
  });

  it('AgentCoding Bot 不渲染「…」(#2990 门控保留,纯点击直达)', () => {
    const { container } = render(
      <ConversationBotItem
        bot={{ ...bot, isAgentCodingBot: true, chatable: false, spaceId: '101' }}
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
        onOpenBotManagement={noop}
      />,
    );
    expect(screen.queryByRole('button', { name: '更多操作' })).not.toBeInTheDocument();
    expect(container.querySelectorAll('button').length).toBe(1);
  });

  it('菜单打开期间操作区钉住:strip 含 data-state=open 钉住类(静态类断言,jsdom 不算选择器)', () => {
    const { container } = renderBotItem({ bot: { spaceId: '101' }, onOpenBotManagement: noop });
    const actions = Array.from(container.querySelectorAll('div')).find((el) =>
      el.className.includes('right-[34px]'),
    ) as HTMLElement;
    expect(actions.className).toContain('group-has-[[data-state=open]]:opacity-100');
  });

  it('origin=others 只读视角不渲染「更多操作」入口', () => {
    render(
      <ConversationBotItem
        bot={{ ...bot, spaceId: '101' }}
        section="managed"
        expanded={false}
        selectedSessionId={null}
        origin="others"
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
        onOpenBotManagement={noop}
      />,
    );
    // 只读视角检索/筛选保留,「更多操作」不渲染。
    expect(screen.queryByRole('button', { name: '更多操作' })).not.toBeInTheDocument();
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

  it('徽标位于名称下方独立行,超长模板名按整行截断且不新增 Tooltip', () => {
    renderBadgeRow({
      isAgentCodingBot: true,
      chatable: false,
      templateName: '非常长非常长非常长非常长模板名',
    });
    const label = screen.getByText('非常长非常长非常长非常长模板名');
    expect(label).toHaveClass('max-w-full', 'truncate');
    expect(label.closest('[data-state="tooltip"]')).toBeNull();
  });

  it.each([
    ['openclaw', 'OpenClaw', 'text-primary'],
    ['teclaw', 'TEClaw', 'text-success'],
    ['claude_code', 'ClaudeCode', 'text-purple'],
  ] as const)('引擎 %s 徽章着色与 Bot 管理一致（%s）', (engine, label, toneClass) => {
    renderBadgeRow({ engine });
    expect(screen.getByText(label)).toHaveClass(toneClass);
  });

  it('AgentCoding 模板徽章保持模板名标签，但色彩取引擎规则（ClaudeCode 模板=紫）', () => {
    renderBadgeRow({ isAgentCodingBot: true, chatable: false, engine: 'claude_code', templateName: 'Coding Bot' });
    // 标签口径不变（模板名优先），颜色与 Bot 管理「标签」列同源。
    expect(screen.getByText('Coding Bot')).toHaveClass('text-purple');
  });

  it('未知引擎徽章回退中性色（不给未识别 Bot 涂装误导色）', () => {
    renderBadgeRow({ engine: 'unknown_engine' });
    expect(screen.getByText('unknown_engine')).toHaveClass('text-muted-foreground');
  });
});
