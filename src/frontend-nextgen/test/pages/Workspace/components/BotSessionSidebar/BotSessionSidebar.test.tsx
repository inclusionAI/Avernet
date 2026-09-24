/** @jest-environment jsdom */
import { BotSessionSidebar } from '@/pages/Workspace/components/BotSessionSidebar/index';
import { ChatBotView } from '@/services/workspace/botSessionService';
import { describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

// 本套件重度使用 Radix 组件（Popover/菜单/收藏切换等），jsdom 下异步渲染开销大（20s+/例），
// 默认 30s 在并行/高负载下随机临界超时。统一放宽至 60s（仅本文件生效，不改全局 testTimeout，不改任何断言）。
jest.setTimeout(60000);

beforeEach(() => {
  // SessionCard 标题截断检测依赖 ResizeObserver（jsdom 需 mock，模式同项目其他测试）。
  Object.defineProperty(globalThis, 'ResizeObserver', {
    configurable: true,
    value: class ResizeObserverMock {
      observe() {}

      unobserve() {}

      disconnect() {}
    },
  });
});

const bots: ChatBotView[] = [
  {
    botId: 'b:1',
    realBotId: 'b',
    ownerId: '1',
    displayName: '可聊Bot',
    online: true,
    chatable: true,
    engine: 'openclaw',
  },
  {
    botId: 'plain',
    realBotId: 'plain',
    displayName: '不可聊Bot',
    online: true,
    chatable: false,
    engine: 'claude_code',
  },
];
const sessionsByBotId = {
  'b:1': [
    {
      sessionId: 's1',
      botId: 'b:1',
      title: '会话1',
      messageCount: 0,
      gmtModified: '2026-08-30T12:00:00',
      gmtCreate: '2026-08-29T12:00:00',
    },
  ],
};
const noopBool = async () => false;
const noopAsync = async (): Promise<void> => {};
const noop = () => {};

describe('BotSessionSidebar', () => {
  it('可聊 bot 显示名称,不可聊 bot 显示并带禁用提示', () => {
    const { container } = render(
      <BotSessionSidebar
        view="chat"
        availableViews={['chat', 'group']}
        onViewChange={() => {}}
        chatBots={bots}
        friendBots={[]}
        isMyBotsLoading={false}
        isFriendBotsLoading={false}
        expandedBotSectionKey={{ 'b:1': 'mine' }}
        expandedBotIds={{ 'b:1': true }}
        sessionsByBotId={sessionsByBotId}
        sessionPageMetaByBotId={{ 'b:1': { total: 1, hasMore: false, nextPage: 2, isLoadingMore: false } }}
        favoriteSessionPageMetaByBotId={{ 'b:1': { total: 0, hasMore: false, nextPage: 2, isLoadingMore: false } }}
        isSessionsLoading={false}
        selectedBotSessionId="s1"
        onToggleBotExpanded={() => {}}
        onSelectSession={() => {}}
        onCreateSession={() => {}}
        onDeleteSession={noopBool}
        onRenameSession={noopBool}
        onClearSessionContext={noopBool}
        onToggleFavorite={noopBool}
        onLoadFavorites={noopAsync}
        onOpenPublicBots={noop}
      />,
    );
    expect(container.querySelector('.self-start')).not.toBeInTheDocument();
    const activeTab = screen.getByRole('button', { name: '对话' });
    const inactiveTab = screen.getByRole('button', { name: '协作群' });
    const botTrigger = screen.getByRole('button', { name: '可聊Bot' });
    expect(activeTab).toHaveAttribute('aria-pressed', 'true');
    expect(inactiveTab).toHaveAttribute('aria-pressed', 'false');
    expect(activeTab).toHaveClass('bg-background', 'text-primary', 'shadow-sm');
    expect(inactiveTab).toHaveClass('text-muted-foreground');
    expect(screen.queryByRole('button', { name: '发起协作' })).not.toBeInTheDocument();
    expect(botTrigger.parentElement).toHaveClass('min-h-16', 'bg-muted', 'px-4', 'py-2.5');
    expect(botTrigger.parentElement?.querySelector('svg.lucide-chevron-down')).toBeInTheDocument();
    expect(botTrigger).toHaveClass('gap-3', 'px-0', 'py-1');
    const botAvatar = botTrigger.firstElementChild as HTMLElement;
    expect(botAvatar).toHaveClass('bg-primary/15', 'text-primary', 'ring-primary/30');
    expect(screen.getByText('可聊Bot')).toHaveClass('text-sm', 'font-medium');
    expect(screen.getByText('可聊Bot')).not.toHaveClass('font-semibold');
    expect(screen.getByText('OpenClaw').parentElement).toHaveClass('text-xs', 'leading-4');
    expect(screen.getByText('不可聊Bot')).toBeInTheDocument();
    expect(screen.getByText('OpenClaw')).toBeInTheDocument();
    expect(screen.getByText('ClaudeCode')).toBeInTheDocument();
    expect(screen.getByText('会话1')).toBeInTheDocument();
    expect(screen.getByText('08/30')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '会话更多操作' })).toBeInTheDocument();
    // v1.4：条数副行移至顶栏 summary，列表行不再显示“N 条消息”并统一紧凑单行
    expect(screen.queryByText(/条消息/)).not.toBeInTheDocument();
    const sessionTrigger = screen.getByRole('button', { name: '会话1' });
    expect(sessionTrigger).toHaveClass('min-h-12', 'items-center', 'py-2');
    expect(sessionTrigger.parentElement).toHaveClass('min-h-12');
  });

  it('v1.5：Bot 行操作区绝对定位悬浮不占位，badge 行 hover 换行展开兜底', () => {
    render(
      <BotSessionSidebar
        view="chat"
        availableViews={['chat', 'group']}
        onViewChange={() => {}}
        chatBots={bots}
        friendBots={[]}
        isMyBotsLoading={false}
        isFriendBotsLoading={false}
        expandedBotSectionKey={{ 'b:1': 'mine' }}
        expandedBotIds={{ 'b:1': true }}
        sessionsByBotId={sessionsByBotId}
        sessionPageMetaByBotId={{ 'b:1': { total: 1, hasMore: false, nextPage: 2, isLoadingMore: false } }}
        favoriteSessionPageMetaByBotId={{ 'b:1': { total: 0, hasMore: false, nextPage: 2, isLoadingMore: false } }}
        isSessionsLoading={false}
        selectedBotSessionId="s1"
        onToggleBotExpanded={() => {}}
        onSelectSession={() => {}}
        onCreateSession={() => {}}
        onDeleteSession={noopBool}
        onRenameSession={noopBool}
        onClearSessionContext={noopBool}
        onToggleFavorite={noopBool}
        onLoadFavorites={noopAsync}
        onOpenPublicBots={noop}
      />,
    );

    // 操作区满行覆盖（inset-y-0 撑满行高）+ 工具类遮盖底 + 左缘 mask 渐隐 + 显隐语义保留（v1.5 同构）。
    const newSessionBtn = screen.getByRole('button', { name: '新建会话' });
    const actions = newSessionBtn.parentElement;
    expect(actions).toHaveClass(
      'absolute',
      'inset-y-0',
      'right-[30px]',
      'z-10',
      'mask-[linear-gradient(to_right,transparent,black_20px)]',
      'sidebar-actions-cover-selected',
    );
    expect(actions).toHaveClass('group-hover:opacity-100', 'group-focus-within:opacity-100');
    // badge 辅助行：单行 truncate + hover 右移让位操作区（悬浮避让，行高恒定）。
    const badgeRow = screen.getByText('OpenClaw').parentElement;
    expect(badgeRow).toHaveClass('truncate', 'group-hover:pr-20');
  });

  it('收起态保留对话与协作群快捷切换图标', () => {
    const onViewChange = jest.fn();
    render(
      <BotSessionSidebar
        view="chat"
        availableViews={['chat', 'group']}
        onViewChange={onViewChange}
        chatBots={bots}
        friendBots={[]}
        isMyBotsLoading={false}
        isFriendBotsLoading={false}
        expandedBotSectionKey={{}}
        expandedBotIds={{}}
        sessionsByBotId={{}}
        isSessionsLoading={false}
        selectedBotSessionId={null}
        onToggleBotExpanded={noop}
        onSelectSession={noop}
        onCreateSession={noop}
        onDeleteSession={noopBool}
        onRenameSession={noopBool}
        onClearSessionContext={noopBool}
        onToggleFavorite={noopBool}
        onLoadFavorites={noopAsync}
        onOpenPublicBots={noop}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: '收起对话协作左栏' }));

    expect(screen.getByRole('button', { name: '切换到对话' })).toHaveAttribute('aria-pressed', 'true');
    fireEvent.click(screen.getByRole('button', { name: '切换到协作群' }));
    expect(onViewChange).toHaveBeenCalledWith('group');
    fireEvent.click(screen.getByRole('button', { name: '展开对话协作左栏' }));
  });

  it('Bot 身份无可协作 Bot 时引导前往公开 Bot', () => {
    const onOpenPublicBots = jest.fn();
    render(
      <BotSessionSidebar
        view="chat"
        availableViews={['chat', 'group']}
        onViewChange={noop}
        identities={[{ id: 'bot-viewer', name: '当前 Bot', kind: 'bot', avatar: 'B' }]}
        activeIdentityId="bot-viewer"
        chatBots={[]}
        friendBots={[]}
        isMyBotsLoading={false}
        isFriendBotsLoading={false}
        expandedBotSectionKey={{}}
        expandedBotIds={{}}
        sessionsByBotId={{}}
        isSessionsLoading={false}
        selectedBotSessionId={null}
        onToggleBotExpanded={noop}
        onSelectSession={noop}
        onCreateSession={noop}
        onDeleteSession={noopBool}
        onRenameSession={noopBool}
        onClearSessionContext={noopBool}
        onToggleFavorite={noopBool}
        onLoadFavorites={noopAsync}
        onOpenPublicBots={onOpenPublicBots}
      />,
    );

    expect(screen.getByText('暂无可协作的 Bot')).toBeInTheDocument();
    expect(screen.getByText('可前往「公开 Bot」发现并添加 Bot。')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '前往公开 Bot' }));
    expect(onOpenPublicBots).toHaveBeenCalledTimes(1);
  });

  it('Bot 分组错误不伪装成空态，并提供重试入口', () => {
    const onRetry = jest.fn();
    render(
      <BotSessionSidebar
        view="chat"
        availableViews={['chat', 'group']}
        onViewChange={() => {}}
        identities={[{ id: 'identity:me', name: '示例用户', kind: 'user', avatar: '风' }]}
        activeIdentityId="identity:me"
        chatBots={[]}
        friendBots={[]}
        isMyBotsLoading={false}
        myBotsError="管理 Bot 加载失败"
        onRetryMyBots={onRetry}
        isFriendBotsLoading={false}
        expandedBotSectionKey={{}}
        expandedBotIds={{}}
        sessionsByBotId={{}}
        isSessionsLoading={false}
        selectedBotSessionId={null}
        onToggleBotExpanded={() => {}}
        onSelectSession={() => {}}
        onCreateSession={() => {}}
        onDeleteSession={noopBool}
        onRenameSession={noopBool}
        onClearSessionContext={noopBool}
        onToggleFavorite={noopBool}
        onLoadFavorites={noopAsync}
        onOpenPublicBots={noop}
      />,
    );
    expect(screen.getByRole('alert')).toHaveTextContent('管理 Bot 加载失败');
    fireEvent.click(screen.getByRole('button', { name: '重试' }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it('Bot 会话加载失败时保留会话范围并提供局部重试', () => {
    const onReloadBot = jest.fn().mockResolvedValue(undefined);
    render(
      <BotSessionSidebar
        view="chat"
        availableViews={['chat', 'group']}
        onViewChange={() => {}}
        identities={[{ id: 'identity:me', name: '示例用户', kind: 'user', avatar: '风' }]}
        activeIdentityId="identity:me"
        chatBots={[bots[0]]}
        friendBots={[]}
        isMyBotsLoading={false}
        isFriendBotsLoading={false}
        expandedBotSectionKey={{ 'b:1': 'mine' }}
        expandedBotIds={{ 'b:1': true }}
        sessionsByBotId={{}}
        sessionPageMetaByBotId={{
          'b:1': { total: 0, hasMore: false, nextPage: 1, isLoadingMore: false, error: '会话加载失败' },
        }}
        isSessionsLoading={false}
        selectedBotSessionId={null}
        onToggleBotExpanded={() => {}}
        onSelectSession={() => {}}
        onCreateSession={() => {}}
        onDeleteSession={noopBool}
        onRenameSession={noopBool}
        onClearSessionContext={noopBool}
        onToggleFavorite={noopBool}
        onLoadFavorites={noopAsync}
        onReloadBot={onReloadBot}
        onOpenPublicBots={noop}
      />,
    );
    expect(screen.getByRole('alert')).toHaveTextContent('会话加载失败');
    fireEvent.click(screen.getByRole('button', { name: '重试' }));
    expect(onReloadBot).toHaveBeenCalledWith('b:1');
  });

  it('对话列表向下滚动时一级 Tab 吸顶', () => {
    render(
      <BotSessionSidebar
        view="chat"
        availableViews={['chat', 'group']}
        onViewChange={() => {}}
        chatBots={bots}
        friendBots={[]}
        isMyBotsLoading={false}
        isFriendBotsLoading={false}
        expandedBotSectionKey={{}}
        expandedBotIds={{}}
        sessionsByBotId={{}}
        isSessionsLoading={false}
        selectedBotSessionId={null}
        onToggleBotExpanded={() => {}}
        onSelectSession={() => {}}
        onCreateSession={() => {}}
        onDeleteSession={noopBool}
        onRenameSession={noopBool}
        onClearSessionContext={noopBool}
        onToggleFavorite={noopBool}
        onLoadFavorites={noopAsync}
        onOpenPublicBots={noop}
      />,
    );

    const tabGroup = screen.getByRole('group', { name: '工作区类型' });
    expect(tabGroup.parentElement).toHaveClass('h-10', 'items-center');
    expect(tabGroup.parentElement?.parentElement).toHaveClass('sticky', 'top-0', 'z-20', 'bg-muted/20');
  });

  it('对话筛选行与协作群搜索行使用统一上下留白', () => {
    render(
      <BotSessionSidebar
        view="chat"
        availableViews={['chat', 'group']}
        onViewChange={() => {}}
        chatBots={bots}
        friendBots={[]}
        isMyBotsLoading={false}
        isFriendBotsLoading={false}
        expandedBotSectionKey={{}}
        expandedBotIds={{}}
        sessionsByBotId={{}}
        isSessionsLoading={false}
        selectedBotSessionId={null}
        onToggleBotExpanded={() => {}}
        onSelectSession={() => {}}
        onCreateSession={() => {}}
        onDeleteSession={noopBool}
        onRenameSession={noopBool}
        onClearSessionContext={noopBool}
        onToggleFavorite={noopBool}
        onLoadFavorites={noopAsync}
        onOpenPublicBots={noop}
      />,
    );

    const searchInput = screen.getByRole('textbox', { name: '搜索 Bot' });
    expect(searchInput.parentElement?.parentElement).toHaveClass('my-2', 'px-4');
  });

  it('Bot 行辅助信息使用 bots 接口 engine 字段的统一展示名', () => {
    render(
      <BotSessionSidebar
        view="chat"
        availableViews={['chat', 'group']}
        onViewChange={() => {}}
        chatBots={[
          { ...bots[0], engine: 'TEClaw' },
          { ...bots[1], engine: 'Hermes' },
        ]}
        friendBots={[]}
        isMyBotsLoading={false}
        isFriendBotsLoading={false}
        expandedBotSectionKey={{}}
        expandedBotIds={{}}
        sessionsByBotId={{}}
        isSessionsLoading={false}
        selectedBotSessionId={null}
        onToggleBotExpanded={() => {}}
        onSelectSession={() => {}}
        onCreateSession={() => {}}
        onDeleteSession={noopBool}
        onRenameSession={noopBool}
        onClearSessionContext={noopBool}
        onToggleFavorite={noopBool}
        onLoadFavorites={noopAsync}
        onOpenPublicBots={noop}
      />,
    );

    expect(screen.getByText('TEClaw')).toBeInTheDocument();
    expect(screen.getByText('Hermes')).toBeInTheDocument();
  });

  it('Bot 名称搜索框为 focus ring 预留水平空间', () => {
    render(
      <BotSessionSidebar
        view="chat"
        availableViews={['chat', 'group']}
        onViewChange={() => {}}
        chatBots={bots}
        friendBots={[]}
        isMyBotsLoading={false}
        isFriendBotsLoading={false}
        expandedBotSectionKey={{ 'b:1': 'mine' }}
        expandedBotIds={{ 'b:1': true }}
        sessionsByBotId={sessionsByBotId}
        sessionPageMetaByBotId={{ 'b:1': { total: 1, hasMore: false, nextPage: 2, isLoadingMore: false } }}
        favoriteSessionPageMetaByBotId={{ 'b:1': { total: 0, hasMore: false, nextPage: 2, isLoadingMore: false } }}
        isSessionsLoading={false}
        selectedBotSessionId={null}
        onToggleBotExpanded={() => {}}
        onSelectSession={() => {}}
        onCreateSession={() => {}}
        onDeleteSession={noopBool}
        onRenameSession={noopBool}
        onClearSessionContext={noopBool}
        onToggleFavorite={noopBool}
        onLoadFavorites={noopAsync}
        onOpenPublicBots={noop}
      />,
    );
    const searchInput = screen.getByRole('textbox', { name: '搜索 Bot' });
    expect(searchInput.parentElement?.parentElement).toHaveClass('px-4');
  });

  it('使用当前身份名称展示 Bot 分组标题', () => {
    render(
      <BotSessionSidebar
        view="chat"
        availableViews={['chat', 'group']}
        onViewChange={() => {}}
        identities={[{ id: 'identity:me', name: '示例用户', kind: 'user', avatar: '风' }]}
        activeIdentityId="identity:me"
        chatBots={bots}
        friendBots={[]}
        isMyBotsLoading={false}
        isFriendBotsLoading={false}
        expandedBotSectionKey={{}}
        expandedBotIds={{}}
        sessionsByBotId={{}}
        isSessionsLoading={false}
        selectedBotSessionId={null}
        onToggleBotExpanded={() => {}}
        onSelectSession={() => {}}
        onCreateSession={() => {}}
        onDeleteSession={noopBool}
        onRenameSession={noopBool}
        onClearSessionContext={noopBool}
        onToggleFavorite={noopBool}
        onLoadFavorites={noopAsync}
        onOpenPublicBots={noop}
      />,
    );

    expect(screen.queryByRole('button', { name: '当前协作身份：示例用户' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '示例用户管理的 Bot (2)' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '示例用户管理的 Bot (2)' })).toHaveClass('min-h-9');
    expect(screen.getByRole('button', { name: '示例用户的好友 Bot (0)' })).toBeInTheDocument();
  });

  it('Bot 卡片触发区支持键盘展开,新建会话不触发展开', async () => {
    const onToggle = jest.fn();
    const onCreateSession = jest.fn();
    render(
      <BotSessionSidebar
        view="chat"
        availableViews={['chat', 'group']}
        onViewChange={() => {}}
        chatBots={bots}
        friendBots={[]}
        isMyBotsLoading={false}
        isFriendBotsLoading={false}
        expandedBotSectionKey={{ 'b:1': 'mine' }}
        expandedBotIds={{ 'b:1': true }}
        sessionsByBotId={sessionsByBotId}
        sessionPageMetaByBotId={{ 'b:1': { total: 1, hasMore: false, nextPage: 2, isLoadingMore: false } }}
        favoriteSessionPageMetaByBotId={{ 'b:1': { total: 0, hasMore: false, nextPage: 2, isLoadingMore: false } }}
        isSessionsLoading={false}
        selectedBotSessionId="s1"
        onToggleBotExpanded={onToggle}
        onSelectSession={() => {}}
        onCreateSession={onCreateSession}
        onDeleteSession={noopBool}
        onRenameSession={noopBool}
        onClearSessionContext={noopBool}
        onToggleFavorite={noopBool}
        onLoadFavorites={noopAsync}
        onOpenPublicBots={noop}
      />,
    );
    const botTrigger = screen.getByRole('button', { name: /^可聊Bot/ });
    botTrigger.focus();
    await userEvent.setup().keyboard('{Enter}');
    expect(onToggle).toHaveBeenCalledWith('b:1', 'mine');
    const createSessionButton = screen.getByRole('button', { name: '新建会话' });
    expect(createSessionButton).toBeInTheDocument();
    expect(createSessionButton).toHaveClass(
      'h-6',
      'w-6',
      'rounded-md',
      'text-muted-foreground',
      'hover:bg-primary/10',
      'hover:text-primary',
    );
    const scopeButton = screen.getByRole('button', { name: '会话范围：全部会话' });
    expect(scopeButton).toHaveClass('h-6', 'w-6');
    expect(scopeButton.querySelector('svg.lucide-list-filter')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Bot操作' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '管理 Bot' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '已管理 Bot (2)' })).toHaveClass('rounded-none', 'min-h-9');
    fireEvent.click(screen.getByRole('button', { name: '新建会话' }));
    expect(onCreateSession).toHaveBeenCalledWith('b:1');
    expect(onToggle).toHaveBeenCalledTimes(1);
  });

  it('TEClaw Bot 保留新建会话，但不展示收藏入口或预加载收藏会话', () => {
    const teclawBot: ChatBotView = {
      ...bots[0],
      botId: 'teclaw:1',
      realBotId: 'teclaw',
      displayName: 'TEClaw Bot',
      engine: 'teclaw',
    };
    const onLoadFavorites = jest.fn().mockResolvedValue(undefined);
    const onCreateSession = jest.fn();

    render(
      <BotSessionSidebar
        view="chat"
        availableViews={['chat', 'group']}
        onViewChange={() => {}}
        chatBots={[teclawBot]}
        friendBots={[]}
        isMyBotsLoading={false}
        isFriendBotsLoading={false}
        expandedBotSectionKey={{ 'teclaw:1': 'mine' }}
        expandedBotIds={{ 'teclaw:1': true }}
        sessionsByBotId={{ 'teclaw:1': sessionsByBotId['b:1'] }}
        sessionPageMetaByBotId={{ 'teclaw:1': { total: 1, hasMore: false, nextPage: 2, isLoadingMore: false } }}
        favoriteSessionPageMetaByBotId={{}}
        isSessionsLoading={false}
        selectedBotSessionId="s1"
        onToggleBotExpanded={() => {}}
        onSelectSession={() => {}}
        onCreateSession={onCreateSession}
        onDeleteSession={noopBool}
        onRenameSession={noopBool}
        onClearSessionContext={noopBool}
        onToggleFavorite={noopBool}
        onLoadFavorites={onLoadFavorites}
        onOpenPublicBots={noop}
      />,
    );

    const createSessionButton = screen.getByRole('button', { name: '新建会话' });
    fireEvent.click(createSessionButton);
    expect(onCreateSession).toHaveBeenCalledWith('teclaw:1');
    expect(screen.queryByRole('button', { name: '会话范围：全部会话' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '收藏会话' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '取消收藏' })).not.toBeInTheDocument();
    expect(onLoadFavorites).not.toHaveBeenCalled();
  });

  it('v1.4：Bot 会话行未收藏星标默认隐藏，点击仍直达切换收藏', () => {
    const onToggleFavorite = jest.fn().mockResolvedValue(true);
    render(
      <BotSessionSidebar
        view="chat"
        availableViews={['chat', 'group']}
        onViewChange={() => {}}
        chatBots={bots}
        friendBots={[]}
        isMyBotsLoading={false}
        isFriendBotsLoading={false}
        expandedBotSectionKey={{ 'b:1': 'mine' }}
        expandedBotIds={{ 'b:1': true }}
        sessionsByBotId={sessionsByBotId}
        sessionPageMetaByBotId={{ 'b:1': { total: 1, hasMore: false, nextPage: 2, isLoadingMore: false } }}
        favoriteSessionPageMetaByBotId={{ 'b:1': { total: 0, hasMore: false, nextPage: 2, isLoadingMore: false } }}
        isSessionsLoading={false}
        selectedBotSessionId={null}
        onToggleBotExpanded={() => {}}
        onSelectSession={() => {}}
        onCreateSession={() => {}}
        onDeleteSession={noopBool}
        onRenameSession={noopBool}
        onClearSessionContext={noopBool}
        onToggleFavorite={onToggleFavorite}
        onLoadFavorites={noopAsync}
        onOpenPublicBots={noop}
      />,
    );

    // 星标未收藏态默认隐藏（悬停/聚焦显现），点击仍直达切换
    const star = screen.getByRole('button', { name: '收藏会话' });
    expect(star.querySelector('svg.lucide-star')).toBeInTheDocument();
    expect(star).toHaveClass('opacity-0', 'group-hover:opacity-100', 'group-focus-within:opacity-100');
    fireEvent.click(star);
    expect(onToggleFavorite).toHaveBeenCalledWith('b:1', 's1');
  });

  it('Bot 会话更多操作保留会话管理能力', () => {
    render(
      <BotSessionSidebar
        view="chat"
        availableViews={['chat', 'group']}
        onViewChange={() => {}}
        chatBots={bots}
        friendBots={[]}
        isMyBotsLoading={false}
        isFriendBotsLoading={false}
        expandedBotSectionKey={{ 'b:1': 'mine' }}
        expandedBotIds={{ 'b:1': true }}
        sessionsByBotId={sessionsByBotId}
        sessionPageMetaByBotId={{ 'b:1': { total: 1, hasMore: false, nextPage: 2, isLoadingMore: false } }}
        favoriteSessionPageMetaByBotId={{ 'b:1': { total: 0, hasMore: false, nextPage: 2, isLoadingMore: false } }}
        isSessionsLoading={false}
        selectedBotSessionId="s1"
        onToggleBotExpanded={() => {}}
        onSelectSession={() => {}}
        onCreateSession={() => {}}
        onDeleteSession={noopBool}
        onRenameSession={noopBool}
        onClearSessionContext={noopBool}
        onToggleFavorite={noopBool}
        onLoadFavorites={noopAsync}
        onOpenPublicBots={noop}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: '会话更多操作' }));
    fireEvent.click(screen.getByRole('button', { name: '编辑标题' }));
    expect(screen.getByText('编辑会话标题')).toBeInTheDocument();
  });

  it('metadata 缺失的好友 Bot 灰显禁用且不展示会话操作', async () => {
    const onToggle = jest.fn();
    const onCreateSession = jest.fn();
    render(
      <BotSessionSidebar
        view="chat"
        availableViews={['chat', 'group']}
        onViewChange={noop}
        identities={[{ id: 'identity:me', name: '示例用户', kind: 'user', avatar: '风' }]}
        activeIdentityId="identity:me"
        chatBots={[]}
        friendBots={[
          {
            botId: 'missing:owner',
            realBotId: 'missing',
            ownerId: 'owner',
            displayName: 'missing:owner',
            online: false,
            chatable: false,
            isFriendBot: true,
          },
        ]}
        isMyBotsLoading={false}
        isFriendBotsLoading={false}
        expandedBotSectionKey={{}}
        expandedBotIds={{}}
        sessionsByBotId={{}}
        isSessionsLoading={false}
        selectedBotSessionId={null}
        onToggleBotExpanded={onToggle}
        onSelectSession={noop}
        onCreateSession={onCreateSession}
        onDeleteSession={noopBool}
        onRenameSession={noopBool}
        onClearSessionContext={noopBool}
        onToggleFavorite={noopBool}
        onLoadFavorites={noopAsync}
        onOpenPublicBots={noop}
      />,
    );

    const trigger = screen.getByRole('button', { name: '不可用 Bot' });
    expect(trigger).toHaveAttribute('aria-disabled', 'true');
    expect(trigger).toHaveClass('opacity-50');
    expect(screen.queryByText('暂不支持单聊')).not.toBeInTheDocument();
    expect(screen.getByText('状态异常')).toHaveClass('bg-muted', 'text-muted-foreground');
    await userEvent.setup().hover(screen.getByText('missing:owner'));
    expect(await screen.findByText('该 Bot 状态异常')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '新建会话' })).not.toBeInTheDocument();
    fireEvent.click(trigger);
    expect(onToggle).not.toHaveBeenCalled();
    expect(onCreateSession).not.toHaveBeenCalled();
  });

  it('不可聊 bot 点击不触发选择', () => {
    const onToggle = jest.fn();
    render(
      <BotSessionSidebar
        view="chat"
        availableViews={['chat', 'group']}
        onViewChange={() => {}}
        chatBots={bots}
        friendBots={[]}
        isMyBotsLoading={false}
        isFriendBotsLoading={false}
        expandedBotSectionKey={{}}
        expandedBotIds={{}}
        sessionsByBotId={{}}
        isSessionsLoading={false}
        selectedBotSessionId={null}
        onToggleBotExpanded={onToggle}
        onSelectSession={() => {}}
        onCreateSession={() => {}}
        onDeleteSession={noopBool}
        onRenameSession={noopBool}
        onClearSessionContext={noopBool}
        onToggleFavorite={noopBool}
        onLoadFavorites={noopAsync}
        onOpenPublicBots={noop}
      />,
    );
    // 不可聊 bot(plain)点击不应展开
    const plainCard = screen.getByRole('button', { name: '不可用 Bot' });
    fireEvent.click(plainCard);
    expect(onToggle).not.toHaveBeenCalled();
  });

  it('Bot 展开与承载当前会话时使用一致选中指示', () => {
    const { rerender } = render(
      <BotSessionSidebar
        view="chat"
        availableViews={['chat', 'group']}
        onViewChange={() => {}}
        chatBots={[bots[0]]}
        friendBots={[]}
        isMyBotsLoading={false}
        isFriendBotsLoading={false}
        expandedBotSectionKey={{ 'b:1': 'mine' }}
        expandedBotIds={{ 'b:1': true }}
        sessionsByBotId={sessionsByBotId}
        isSessionsLoading={false}
        selectedBotSessionId={null}
        onToggleBotExpanded={() => {}}
        onSelectSession={() => {}}
        onCreateSession={() => {}}
        onDeleteSession={noopBool}
        onRenameSession={noopBool}
        onClearSessionContext={noopBool}
        onToggleFavorite={noopBool}
        onLoadFavorites={noopAsync}
        onOpenPublicBots={noop}
      />,
    );

    let botRow = screen.getByRole('button', { name: '可聊Bot' }).parentElement;
    expect(botRow).toHaveClass('bg-muted');
    expect(botRow?.querySelector('[class~="w-[3px]"][class~="bg-primary"]')).toBeInTheDocument();

    rerender(
      <BotSessionSidebar
        view="chat"
        availableViews={['chat', 'group']}
        onViewChange={() => {}}
        chatBots={[bots[0]]}
        friendBots={[]}
        isMyBotsLoading={false}
        isFriendBotsLoading={false}
        expandedBotSectionKey={{}}
        expandedBotIds={{}}
        sessionsByBotId={sessionsByBotId}
        isSessionsLoading={false}
        selectedBotSessionId="s1"
        onToggleBotExpanded={() => {}}
        onSelectSession={() => {}}
        onCreateSession={() => {}}
        onDeleteSession={noopBool}
        onRenameSession={noopBool}
        onClearSessionContext={noopBool}
        onToggleFavorite={noopBool}
        onLoadFavorites={noopAsync}
        onOpenPublicBots={noop}
      />,
    );

    botRow = screen.getByRole('button', { name: '可聊Bot' }).parentElement;
    expect(botRow).toHaveClass('bg-muted');
    expect(botRow?.querySelector('[class~="w-[3px]"][class~="bg-primary"]')).toBeInTheDocument();
  });

  it('v1.4：Bot 行操作区默认透明隐藏，悬停/键盘聚焦可显现，选中或展开时常显', () => {
    const { rerender } = render(
      <BotSessionSidebar
        view="chat"
        availableViews={['chat', 'group']}
        onViewChange={() => {}}
        chatBots={bots}
        friendBots={[]}
        isMyBotsLoading={false}
        isFriendBotsLoading={false}
        expandedBotSectionKey={{}}
        expandedBotIds={{}}
        sessionsByBotId={{}}
        isSessionsLoading={false}
        selectedBotSessionId={null}
        onToggleBotExpanded={() => {}}
        onSelectBot={undefined}
        onSelectSession={() => {}}
        onCreateSession={() => {}}
        onDeleteSession={noopBool}
        onRenameSession={noopBool}
        onClearSessionContext={noopBool}
        onToggleFavorite={noopBool}
        onLoadFavorites={noopAsync}
        onLoadMoreSessions={noopAsync}
        onOpenPublicBots={noop}
      />,
    );
    const actions = screen.getByRole('button', { name: '新建会话' }).parentElement as HTMLElement;
    expect(actions).toHaveClass(
      'opacity-0',
      'transition-opacity',
      'group-hover:opacity-100',
      'group-focus-within:opacity-100',
    );
    expect(actions).not.toHaveClass('opacity-100');

    rerender(
      <BotSessionSidebar
        view="chat"
        availableViews={['chat', 'group']}
        onViewChange={() => {}}
        chatBots={bots}
        friendBots={[]}
        isMyBotsLoading={false}
        isFriendBotsLoading={false}
        expandedBotSectionKey={{ 'b:1': 'mine' }}
        expandedBotIds={{ 'b:1': true }}
        sessionsByBotId={sessionsByBotId}
        isSessionsLoading={false}
        selectedBotSessionId={null}
        onToggleBotExpanded={() => {}}
        onSelectBot={undefined}
        onSelectSession={() => {}}
        onCreateSession={() => {}}
        onDeleteSession={noopBool}
        onRenameSession={noopBool}
        onClearSessionContext={noopBool}
        onToggleFavorite={noopBool}
        onLoadFavorites={noopAsync}
        onLoadMoreSessions={noopAsync}
        onOpenPublicBots={noop}
      />,
    );
    const actionsExpanded = screen.getByRole('button', { name: '新建会话' }).parentElement as HTMLElement;
    expect(actionsExpanded).toHaveClass('opacity-100');

    const sessionsArea = screen.getByLabelText('Bot会话列表：可聊Bot');
    expect(sessionsArea).toHaveClass('pl-6');
    expect(sessionsArea).not.toHaveClass('bg-muted/20');
    // 容器不再渲染贯穿干线（before:* 已移除），干线与末行截断由每行 SessionCard 自带。
    expect(sessionsArea).not.toHaveClass('before:absolute');
    const rails = sessionsArea.querySelectorAll('[data-session-tree-rail]');
    expect(rails.length).toBeGreaterThan(0);
    expect(rails[0]).toHaveClass('-left-2', 'top-0', 'bottom-0', 'w-px', 'bg-border');
  });

  it('Bot 会话背景铺满列表宽度，不保留整体缩进', () => {
    render(
      <BotSessionSidebar
        view="chat"
        availableViews={['chat', 'group']}
        onViewChange={() => {}}
        chatBots={[bots[0]]}
        friendBots={[]}
        isMyBotsLoading={false}
        isFriendBotsLoading={false}
        expandedBotSectionKey={{ 'b:1': 'mine' }}
        expandedBotIds={{ 'b:1': true }}
        sessionsByBotId={sessionsByBotId}
        isSessionsLoading={false}
        selectedBotSessionId={null}
        onToggleBotExpanded={() => {}}
        onSelectSession={() => {}}
        onCreateSession={() => {}}
        onDeleteSession={noopBool}
        onRenameSession={noopBool}
        onClearSessionContext={noopBool}
        onToggleFavorite={noopBool}
        onLoadFavorites={noopAsync}
        onOpenPublicBots={noop}
      />,
    );

    const sessionList = screen.getByLabelText('Bot会话列表：可聊Bot');
    // 验收微调：会话区顶部分割线去除（缩进与树形连接线保留）。
    expect(sessionList).not.toHaveClass('border-t');
    expect(sessionList).not.toHaveClass('border-b', 'pl-2');
    expect(sessionList.firstElementChild).not.toHaveClass('border-b', 'pl-2');
  });

  it('Bot 对象行通过纯 Icon 切换全部/已收藏会话', () => {
    render(
      <BotSessionSidebar
        view="chat"
        availableViews={['chat', 'group']}
        onViewChange={() => {}}
        chatBots={bots}
        friendBots={[]}
        isMyBotsLoading={false}
        isFriendBotsLoading={false}
        expandedBotSectionKey={{ 'b:1': 'mine' }}
        expandedBotIds={{ 'b:1': true }}
        sessionsByBotId={sessionsByBotId}
        sessionPageMetaByBotId={{ 'b:1': { total: 1, hasMore: false, nextPage: 2, isLoadingMore: false } }}
        favoriteSessionPageMetaByBotId={{ 'b:1': { total: 0, hasMore: false, nextPage: 2, isLoadingMore: false } }}
        isSessionsLoading={false}
        selectedBotSessionId="s1"
        onToggleBotExpanded={() => {}}
        onSelectSession={() => {}}
        onCreateSession={() => {}}
        onDeleteSession={noopBool}
        onRenameSession={noopBool}
        onClearSessionContext={noopBool}
        onToggleFavorite={noopBool}
        onLoadFavorites={noopAsync}
        onOpenPublicBots={noop}
      />,
    );
    const scopeButton = screen.getByRole('button', { name: '会话范围：全部会话' });
    expect(scopeButton.textContent).toBe('');
    fireEvent.click(scopeButton);
    expect(screen.getByRole('radio', { name: '全部会话 1' })).toHaveAttribute('aria-checked', 'true');
    fireEvent.click(screen.getByRole('radio', { name: '已收藏会话 0' }));
    expect(screen.getByRole('button', { name: '会话范围：已收藏会话' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.queryByText('会话1')).not.toBeInTheDocument();
  });

  it('收起 Bot 切换收藏范围后加载收藏并自动展开对象', () => {
    const onLoadFavorites = jest.fn().mockResolvedValue(undefined);
    const onToggleBotExpanded = jest.fn();
    render(
      <BotSessionSidebar
        view="chat"
        availableViews={['chat', 'group']}
        onViewChange={() => {}}
        chatBots={[bots[0]]}
        friendBots={[]}
        isMyBotsLoading={false}
        isFriendBotsLoading={false}
        expandedBotSectionKey={{}}
        expandedBotIds={{}}
        sessionsByBotId={sessionsByBotId}
        isSessionsLoading={false}
        selectedBotSessionId={null}
        onToggleBotExpanded={onToggleBotExpanded}
        onSelectSession={() => {}}
        onCreateSession={() => {}}
        onDeleteSession={noopBool}
        onRenameSession={noopBool}
        onClearSessionContext={noopBool}
        onToggleFavorite={noopBool}
        onLoadFavorites={onLoadFavorites}
        onOpenPublicBots={noop}
      />,
    );

    const botTrigger = screen.getByRole('button', { name: '可聊Bot' });
    expect(botTrigger).toHaveAttribute('aria-expanded', 'false');
    expect(botTrigger.parentElement?.querySelector('svg.lucide-chevron-right')).toBeInTheDocument();
    const scopeButton = screen.getByRole('button', { name: '会话范围：全部会话' });
    expect(scopeButton.querySelector('svg.lucide-list-filter')).toBeInTheDocument();
    fireEvent.click(scopeButton);
    fireEvent.click(screen.getByRole('radio', { name: '已收藏会话 …' }));

    expect(onLoadFavorites).toHaveBeenCalledWith('b:1');
    expect(onToggleBotExpanded).toHaveBeenCalledWith('b:1', 'mine');
  });

  it('展开 Bot 会话区不再渲染旧会话范围工具栏', () => {
    render(
      <BotSessionSidebar
        view="chat"
        availableViews={['chat', 'group']}
        onViewChange={() => {}}
        chatBots={[bots[0]]}
        friendBots={[]}
        isMyBotsLoading={false}
        isFriendBotsLoading={false}
        expandedBotSectionKey={{ 'b:1': 'mine' }}
        expandedBotIds={{ 'b:1': true }}
        sessionsByBotId={sessionsByBotId}
        isSessionsLoading={false}
        selectedBotSessionId={null}
        onToggleBotExpanded={() => {}}
        onSelectSession={() => {}}
        onCreateSession={() => {}}
        onDeleteSession={noopBool}
        onRenameSession={noopBool}
        onClearSessionContext={noopBool}
        onToggleFavorite={noopBool}
        onLoadFavorites={noopAsync}
        onOpenPublicBots={noop}
      />,
    );

    expect(screen.queryByRole('group', { name: '会话范围筛选' })).not.toBeInTheDocument();
  });

  it('接口返回 AgentCoding Bot 时在管理 Bot 分组展示 Bot 工坊提示链接', async () => {
    const onOpenBotWorkshop = jest.fn();
    render(
      <BotSessionSidebar
        view="chat"
        availableViews={['chat', 'group']}
        onViewChange={() => {}}
        identities={[{ id: 'identity:me', name: '示例用户', kind: 'user', avatar: '风' }]}
        activeIdentityId="identity:me"
        chatBots={bots}
        friendBots={[]}
        isMyBotsLoading={false}
        isFriendBotsLoading={false}
        expandedBotSectionKey={{}}
        expandedBotIds={{}}
        sessionsByBotId={{}}
        isSessionsLoading={false}
        selectedBotSessionId={null}
        onToggleBotExpanded={() => {}}
        onSelectSession={() => {}}
        onCreateSession={() => {}}
        onDeleteSession={noopBool}
        onRenameSession={noopBool}
        onClearSessionContext={noopBool}
        onToggleFavorite={noopBool}
        onLoadFavorites={noopAsync}
        hasAgentCodingBots
        onOpenBotWorkshop={onOpenBotWorkshop}
        onOpenPublicBots={noop}
      />,
    );

    const hintButton = screen.getByRole('button', { name: 'AgentCoding Bot 使用提示' });
    expect(hintButton).toHaveClass('h-7', 'w-7');
    expect(hintButton.closest('.flex.min-h-9')).toContainElement(
      screen.getByRole('button', { name: '示例用户管理的 Bot (2)' }),
    );
    await userEvent.setup().hover(hintButton);
    const workshopLink = await screen.findByRole('link', { name: 'Bot 工坊' });
    expect(workshopLink).toHaveAttribute('href', '/bot-workshop');
    expect(workshopLink.parentElement).toHaveTextContent('AgentCoding Bot 请前往 Bot 工坊 使用');
    fireEvent.click(workshopLink);
    expect(onOpenBotWorkshop).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole('button', { name: 'AgentCoding Bot 请前往 Bot 工坊使用' })).not.toBeInTheDocument();
  });
});
