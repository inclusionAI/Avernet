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
    const activeTab = screen.getByRole('tab', { name: '对话' });
    const inactiveTab = screen.getByRole('tab', { name: '协作群' });
    const botTrigger = screen.getByRole('button', { name: '可聊Bot' });
    expect(activeTab).toHaveClass('h-9', 'border-primary', 'text-primary');
    expect(activeTab).toHaveClass('hover:bg-transparent', 'hover:text-primary');
    expect(inactiveTab).toHaveClass('h-9', 'border-transparent', 'text-muted-foreground');
    expect(inactiveTab).toHaveClass('hover:bg-transparent', 'hover:text-foreground');
    expect(screen.getByRole('button', { name: '添加好友或发起协作' })).toHaveClass('h-9', 'w-9');
    expect(screen.getByRole('button', { name: '添加好友或发起协作' })).toHaveClass(
      'border-input',
      'bg-background',
      'text-muted-foreground',
    );
    expect(screen.getByRole('button', { name: '添加好友或发起协作' })).not.toHaveClass(
      'bg-primary',
      'text-primary-foreground',
    );
    expect(screen.getByRole('button', { name: '添加好友或发起协作' })).not.toHaveClass('lg:hidden');
    expect(botTrigger.parentElement).toHaveClass('min-h-[72px]', 'px-[18px]', 'py-3');
    expect(botTrigger).toHaveClass('gap-2.5', 'px-0', 'py-1');
    expect(screen.getByText('可聊Bot')).toBeInTheDocument();
    expect(screen.getByText('不可聊Bot')).toBeInTheDocument();
    expect(screen.getByText('OpenClaw')).toBeInTheDocument();
    expect(screen.getByText('ClaudeCode')).toBeInTheDocument();
    expect(screen.getByText('会话1')).toBeInTheDocument();
    expect(screen.getByText('08/30 12:00')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '会话更多操作' })).toBeInTheDocument();
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

    const tabList = screen.getByRole('tablist', { name: '工作区类型' });
    expect(tabList.parentElement).toHaveClass('sticky', 'top-0', 'z-20', 'bg-muted');
    expect(tabList.parentElement?.parentElement).toHaveClass('bg-muted');
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

    expect(screen.getByRole('button', { name: '风太管理的 Bot (2)' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '风太管理的 Bot (2)' })).toHaveClass('min-h-10');
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
    expect(createSessionButton).toHaveClass('h-7', 'w-7', 'rounded-md');
    expect(screen.getByRole('group', { name: '会话范围筛选' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Bot操作' })).toHaveClass('rounded-md');
    fireEvent.click(screen.getByRole('button', { name: 'Bot操作' }));
    expect(screen.getByRole('button', { name: '管理 Bot' }).querySelector('svg')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '已管理 Bot (2)' })).toHaveClass('rounded-none', 'min-h-10');
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

  it('展开 bot 显示 全部/已收藏 pill,切到已收藏过滤未收藏会话', () => {
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
    const allTab = screen.getByRole('button', { name: '全部会话 1' });
    expect(allTab).toBeInTheDocument();
    expect(allTab).toHaveClass('border-0', 'rounded-none', 'text-primary');
    const favoriteTab = screen.getByRole('button', { name: '已收藏会话 0' });
    expect(favoriteTab).toHaveClass('text-muted-foreground');
    fireEvent.click(favoriteTab);
    // 切到已收藏 tab，无收藏会话时不显示会话1
    expect(screen.queryByText('会话1')).not.toBeInTheDocument();
  });

  it('接口返回 AgentCoding Bot 时展示 Bot 工坊入口卡片', () => {
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

    const workshopCard = screen.getByRole('button', { name: 'AgentCoding Bot 请前往 Bot 工坊使用' });
    expect(workshopCard).toHaveClass('bg-primary/5', 'border-primary/20');
    fireEvent.click(workshopCard);
    expect(onOpenBotWorkshop).toHaveBeenCalledTimes(1);

    expect(screen.queryByRole('img', { name: 'AgentCoding Bot 使用说明' })).not.toBeInTheDocument();
    expect(workshopCard).toHaveClass('cursor-pointer');
  });
});
