/** @jest-environment jsdom */
import { BotSessionSidebar } from '@/pages/Workspace/components/BotSessionSidebar/index';
import { ChatBotView } from '@/services/workspace/botSessionService';
import { describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen } from '@testing-library/react';

const bots: ChatBotView[] = [
  { botId: 'b:1', realBotId: 'b', ownerId: '1', displayName: '可聊Bot', online: true, chatable: true },
  { botId: 'plain', realBotId: 'plain', displayName: '不可聊Bot', online: true, chatable: false },
];
const sessionsByBotId = {
  'b:1': [{ sessionId: 's1', botId: 'b:1', title: '会话1', messageCount: 0, gmtModified: '', gmtCreate: '' }],
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
    expect(screen.getByRole('button', { name: '对话' })).toBeInTheDocument();
    expect(screen.getByText('可聊Bot')).toBeInTheDocument();
    expect(screen.getByText('不可聊Bot')).toBeInTheDocument();
    expect(screen.getByText('会话1')).toBeInTheDocument();
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
    const plainCard = screen.getByText('不可聊Bot').closest('[role="button"]') as HTMLElement;
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
