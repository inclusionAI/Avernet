/** @jest-environment jsdom */
import { BotSessionSidebar } from '@/pages/Workspace/components/BotSessionSidebar/index';
import { ChatBotView } from '@/services/workspace/botSessionService';
import { describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

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
        onCreateGroup={noop}
        onAddFriend={noop}
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
    expect(screen.getByRole('button', { name: '添加好友或发起协作' })).toHaveClass('h-9', 'w-9', 'rounded-md');
    expect(screen.getByRole('button', { name: '添加好友或发起协作' })).toHaveClass(
      'border-primary/20',
      'bg-primary/5',
      'text-primary',
    );
    expect(screen.getByRole('button', { name: '添加好友或发起协作' })).not.toHaveClass('bg-primary', 'text-primary-foreground');
    expect(screen.getByRole('button', { name: '添加好友或发起协作' })).not.toHaveClass('lg:hidden');
    expect(botTrigger.parentElement).toHaveClass('min-h-16', 'bg-primary/5', 'px-4', 'py-2.5');
    expect(botTrigger.querySelector('svg.lucide-chevron-down')).toBeInTheDocument();
    expect(botTrigger).toHaveClass('gap-3', 'px-0', 'py-1');
    expect(screen.getByText('可聊Bot')).toHaveClass('text-sm', 'font-semibold');
    expect(screen.getByText('OpenClaw').parentElement).toHaveClass('text-xs', 'leading-4');
    expect(screen.getByText('不可聊Bot')).toBeInTheDocument();
    expect(screen.getByText('OpenClaw')).toBeInTheDocument();
    expect(screen.getByText('ClaudeCode')).toBeInTheDocument();
    expect(screen.getByText('会话1')).toBeInTheDocument();
    expect(screen.getByText('08/30')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '会话更多操作' })).toBeInTheDocument();
  });

  it('Bot 分组错误不伪装成空态，并提供重试入口', () => {
    const onRetry = jest.fn();
    render(
      <BotSessionSidebar
        view="chat"
        availableViews={['chat', 'group']}
        onViewChange={() => {}}
        identities={[{ id: 'identity:me', name: '风太', kind: 'user', avatar: '风' }]}
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
        onCreateGroup={noop}
        onAddFriend={noop}
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
        identities={[{ id: 'identity:me', name: '风太', kind: 'user', avatar: '风' }]}
        activeIdentityId="identity:me"
        chatBots={[bots[0]]}
        friendBots={[]}
        isMyBotsLoading={false}
        isFriendBotsLoading={false}
        expandedBotSectionKey={{ 'b:1': 'mine' }}
        expandedBotIds={{ 'b:1': true }}
        sessionsByBotId={{}}
        sessionPageMetaByBotId={{ 'b:1': { total: 0, hasMore: false, nextPage: 1, isLoadingMore: false, error: '会话加载失败' } }}
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
        onCreateGroup={noop}
        onAddFriend={noop}
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
        onCreateGroup={noop}
        onAddFriend={noop}
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
        onCreateGroup={noop}
        onAddFriend={noop}
      />,
    );

    const searchInput = screen.getByRole('textbox', { name: '搜索 Bot' });
    expect(searchInput.parentElement?.parentElement).toHaveClass('my-2', 'px-[18px]');
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
        onCreateGroup={noop}
        onAddFriend={noop}
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
        onCreateGroup={noop}
        onAddFriend={noop}
      />,
    );
    const searchInput = screen.getByRole('textbox', { name: '搜索 Bot' });
    expect(searchInput.parentElement?.parentElement).toHaveClass('px-[18px]');
  });

  it('使用当前身份名称展示 Bot 分组标题', () => {
    render(
      <BotSessionSidebar
        view="chat"
        availableViews={['chat', 'group']}
        onViewChange={() => {}}
        identities={[{ id: 'identity:me', name: '风太', kind: 'user', avatar: '风' }]}
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
        onCreateGroup={noop}
        onAddFriend={noop}
      />,
    );

    expect(screen.queryByRole('button', { name: '当前协作身份：风太' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '风太管理的 Bot (2)' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '风太管理的 Bot (2)' })).toHaveClass('min-h-9');
    expect(screen.getByRole('button', { name: '风太的好友 Bot (0)' })).toBeInTheDocument();
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
        onCreateGroup={noop}
        onAddFriend={noop}
      />,
    );
    const botTrigger = screen.getByRole('button', { name: /^可聊Bot/ });
    botTrigger.focus();
    await userEvent.setup().keyboard('{Enter}');
    expect(onToggle).toHaveBeenCalledWith('b:1', 'mine');
    const createSessionButton = screen.getByRole('button', { name: '新建会话' });
    expect(createSessionButton).toBeInTheDocument();
    expect(createSessionButton).toHaveClass(
      'h-7',
      'w-7',
      'rounded-md',
      'text-muted-foreground',
      'hover:bg-primary/10',
      'hover:text-primary',
    );
    const scopeButton = screen.getByRole('button', { name: '会话范围：全部会话' });
    expect(scopeButton).toHaveClass('h-7', 'w-7');
    expect(scopeButton.querySelector('svg.lucide-list-filter')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Bot操作' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '管理 Bot' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '已管理 Bot (2)' })).toHaveClass('rounded-none', 'min-h-9');
    fireEvent.click(screen.getByRole('button', { name: '新建会话' }));
    expect(onCreateSession).toHaveBeenCalledWith('b:1');
    expect(onToggle).toHaveBeenCalledTimes(1);
  });

  it('Bot 会话更多操作菜单承载收藏入口', () => {
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
        selectedBotSessionId="s1"
        onToggleBotExpanded={() => {}}
        onSelectSession={() => {}}
        onCreateSession={() => {}}
        onDeleteSession={noopBool}
        onRenameSession={noopBool}
        onClearSessionContext={noopBool}
        onToggleFavorite={onToggleFavorite}
        onLoadFavorites={noopAsync}
        onCreateGroup={noop}
        onAddFriend={noop}
      />,
    );

    expect(screen.queryByRole('button', { name: '收藏会话' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '会话更多操作' }));
    fireEvent.click(screen.getByRole('button', { name: '收藏会话' }));
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
        onCreateGroup={noop}
        onAddFriend={noop}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: '会话更多操作' }));
    fireEvent.click(screen.getByRole('button', { name: '编辑标题' }));
    expect(screen.getByText('编辑会话标题')).toBeInTheDocument();
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
        onCreateGroup={noop}
        onAddFriend={noop}
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
        onCreateGroup={noop}
        onAddFriend={noop}
      />,
    );

    let botRow = screen.getByRole('button', { name: '可聊Bot' }).parentElement;
    expect(botRow).toHaveClass('bg-primary/5');
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
        onCreateGroup={noop}
        onAddFriend={noop}
      />,
    );

    botRow = screen.getByRole('button', { name: '可聊Bot' }).parentElement;
    expect(botRow).toHaveClass('bg-primary/5');
    expect(botRow?.querySelector('[class~="w-[3px]"][class~="bg-primary"]')).toBeInTheDocument();
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
        onCreateGroup={noop}
        onAddFriend={noop}
      />,
    );

    const sessionList = screen.getByLabelText('Bot会话列表：可聊Bot');
    expect(sessionList).toHaveClass('border-t');
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
        onCreateGroup={noop}
        onAddFriend={noop}
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
        onCreateGroup={noop}
        onAddFriend={noop}
      />,
    );

    const botTrigger = screen.getByRole('button', { name: '可聊Bot' });
    expect(botTrigger).toHaveAttribute('aria-expanded', 'false');
    expect(botTrigger.querySelector('svg.lucide-chevron-right')).toBeInTheDocument();
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
        onCreateGroup={noop}
        onAddFriend={noop}
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
        identities={[{ id: 'identity:me', name: '风太', kind: 'user', avatar: '风' }]}
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
        onCreateGroup={noop}
        onAddFriend={noop}
      />,
    );

    const hintButton = screen.getByRole('button', { name: 'AgentCoding Bot 使用提示' });
    expect(hintButton).toHaveClass('h-7', 'w-7');
    expect(hintButton.closest('.flex.min-h-9')).toContainElement(
      screen.getByRole('button', { name: '风太管理的 Bot (2)' }),
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
