/** @jest-environment jsdom */
import { useFriendBotSectionSync } from '@/pages/Workspace/hooks/useFriendBotSectionSync';
import type { ChatBotView } from '@/services/workspace/botSessionService';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { beforeEach, expect, it } from '@jest/globals';
import { act, renderHook } from '@testing-library/react';

const friendBot: ChatBotView = {
  botId: 'botfriend:999',
  realBotId: 'botfriend',
  ownerId: '999',
  displayName: '好友Bot',
  online: true,
  chatable: true,
  isFriendBot: true,
};

beforeEach(() => {
  useWorkspaceStore.getState().resetWorkspace();
});

it('好友列表就绪后：已展开但分区被记为 mine 的好友 bot 纠正为 friend', async () => {
  // 冷启动深链 ?bot=好友bot：展开时无法判定归属，store 缺省记 'mine'，
  // 好友分区 expanded 判定（需 ==='friend'）失败 → 行折叠、会话列表不可见。
  useWorkspaceStore.getState().toggleBotExpanded('botfriend:999');
  expect(useWorkspaceStore.getState().expandedBotSectionKey['botfriend:999']).toBe('mine');

  renderHook(() => useFriendBotSectionSync([friendBot]));
  await act(async () => Promise.resolve());

  expect(useWorkspaceStore.getState().expandedBotSectionKey['botfriend:999']).toBe('friend');
});

it('未展开的好友 bot 与 mine 分区 bot 不受影响', async () => {
  // 'b:1' 是「我的 bot」（不在好友列表内），展开后归属应保持 'mine'。
  useWorkspaceStore.getState().toggleBotExpanded('b:1');

  renderHook(() => useFriendBotSectionSync([friendBot]));
  await act(async () => Promise.resolve());

  const s = useWorkspaceStore.getState();
  expect(s.expandedBotSectionKey['b:1']).toBe('mine');
  expect(s.expandedBotIds['botfriend:999']).toBeUndefined();
  expect(s.expandedBotSectionKey['botfriend:999']).toBeUndefined();
});

it('用户手动展开为 friend 分区后不被重复改写', async () => {
  useWorkspaceStore.getState().toggleBotExpanded('botfriend:999');
  useWorkspaceStore.getState().setBotExpandedSection('botfriend:999', 'friend');

  const { rerender } = renderHook(({ bots }: { bots: ChatBotView[] }) => useFriendBotSectionSync(bots), {
    initialProps: { bots: [friendBot] },
  });
  await act(async () => Promise.resolve());
  // 收起后好友列表刷新（新数组引用）不得把 bot 重新展开或改写分区。
  useWorkspaceStore.getState().toggleBotExpanded('botfriend:999');
  rerender({ bots: [{ ...friendBot }] });
  await act(async () => Promise.resolve());

  expect(useWorkspaceStore.getState().expandedBotIds['botfriend:999']).toBeUndefined();
});
