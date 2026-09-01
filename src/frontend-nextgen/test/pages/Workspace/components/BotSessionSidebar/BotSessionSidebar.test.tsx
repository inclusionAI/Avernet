/** @jest-environment jsdom */
import { BotSessionSidebar } from '@/pages/Workspace/components/BotSessionSidebar/index';
import { ChatBotView } from '@/services/workspace/botSessionService';
import { describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

const bots: ChatBotView[] = [
  { botId: 'b:1', realBotId: 'b', ownerId: '1', displayName: '可聊Bot', online: true, chatable: true },
  { botId: 'plain', realBotId: 'plain', displayName: '不可聊Bot', online: true, chatable: false },
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
    const activeTab = screen.getByRole('button', { name: '对话' });
    const inactiveTab = screen.getByRole('button', { name: '协作群' });
    const botTrigger = screen.getByRole('button', { name: '可聊Bot' });
    expect(activeTab.parentElement).toHaveClass('h-9');
    expect(activeTab).toHaveClass('bg-primary/10', 'text-primary', 'hover:bg-primary/15', 'hover:text-primary');
    expect(inactiveTab).toHaveClass(
      'bg-background/60',
      'text-foreground/80',
      'hover:bg-background',
      'hover:text-foreground',
    );
    expect(screen.getByRole('button', { name: '添加好友或发起协作' })).toHaveClass('h-9', 'w-9');
    expect(botTrigger.parentElement).toHaveClass('p-1');
    expect(botTrigger).toHaveClass('gap-2.5', 'px-2', 'py-1.5');
    expect(screen.getByText('可聊Bot')).toBeInTheDocument();
    expect(screen.getByText('不可聊Bot')).toBeInTheDocument();
    expect(screen.getByText('会话1')).toBeInTheDocument();
    expect(screen.getByText('08/30 12:00')).toBeInTheDocument();
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
    expect(searchInput.parentElement?.parentElement).toHaveClass('px-1');
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
        expandedBotSectionKey={{}}
        expandedBotIds={{}}
        sessionsByBotId={{}}
        isSessionsLoading={false}
        selectedBotSessionId={null}
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
    fireEvent.click(screen.getByRole('button', { name: '新建会话' }));
    expect(onCreateSession).toHaveBeenCalledWith('b:1');
    expect(onToggle).toHaveBeenCalledTimes(1);
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
    expect(screen.getByRole('button', { name: '全部 (1)' })).toBeInTheDocument();
    const favoriteTab = screen.getByRole('button', { name: '已收藏 (0)' });
    fireEvent.click(favoriteTab);
    // 切到已收藏 tab，无收藏会话时不显示会话1
    expect(screen.queryByText('会话1')).not.toBeInTheDocument();
  });
});
