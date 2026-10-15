// origin=others 展开区(从 ConversationBotItem 拆出,文件大小阈值):
// 好友目录 / 好友分组的 loading / error / empty / 列表状态收敛在一个纯展示件里。
import type {
  ConversationFriendDirectoryState,
  ConversationFriendGroupView,
} from '@/domain/conversation/types';
import { ListErrorState } from '../../components/ListErrorState';
import { ConversationFriendGroup } from './ConversationFriendGroup';
import { ConversationFriendRowsSkeleton } from './ConversationSkeletons';

export interface ConversationBotItemOthers {
  directory: ConversationFriendDirectoryState;
  /** Store 中是否已有 others 缓存(false 表示尚未加载过,不能判空)。 */
  hasCache: boolean;
  /** 按好友目录顺序排序的分组视图。 */
  friendGroups: ConversationFriendGroupView[];
  /** 跨好友分组的当前选中只读 Session。 */
  selectedSessionId: string | null;
  /** 展开的好友用户(key 为纯用户 ID),来自 Store expandedFriendUserIdsByBotId。 */
  expandedFriendUserIds: Record<string, true>;
  onToggleFriend(friendUserId: string): void;
  onSelectSession(sessionId: string, friendUserId: string): void;
  onRetryDirectory(): void;
  onRetryFriendSessions(friendUserId: string): void;
  onLoadMoreFriendSessions(friendUserId: string): void;
}

// AC-9:全部好友分组均成功且零会话(整组隐藏)→ 整区空态;
// unloaded/loading/error 的分组隐藏 ≠ 空,不算空。
function allFriendGroupsEmpty(groups: ConversationFriendGroupView[]): boolean {
  return (
    groups.length > 0 &&
    groups.every((group) => group.state === 'loaded' && group.sessions.items.length === 0 && !group.sessions.error)
  );
}

/** origin=others 展开区:好友目录 / 好友分组的 loading / error / empty 状态。 */
export function OthersArea({ others }: { others?: ConversationBotItemOthers }) {
  if (!others || !others.hasCache || others.directory.loading) {
    return <ConversationFriendRowsSkeleton rows={3} />;
  }
  if (others.directory.error) {
    return <ListErrorState message={others.directory.error} onRetry={others.onRetryDirectory} />;
  }
  if (others.friendGroups.length === 0 || allFriendGroupsEmpty(others.friendGroups)) {
    return <div className="px-4 py-3 text-xs text-muted-foreground">暂无他人发起的会话</div>;
  }
  return (
    <>
      {others.friendGroups.map((group) => (
        <ConversationFriendGroup
          key={group.friend.userId}
          group={group}
          expanded={others.expandedFriendUserIds[group.friend.userId] === true}
          selectedSessionId={others.selectedSessionId}
          onToggle={() => others.onToggleFriend(group.friend.userId)}
          onSelectSession={(sessionId) => others.onSelectSession(sessionId, group.friend.userId)}
          onRetry={() => others.onRetryFriendSessions(group.friend.userId)}
          onLoadMore={() => void others.onLoadMoreFriendSessions(group.friend.userId)}
        />
      ))}
    </>
  );
}