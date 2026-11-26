/**
 * 公开设置「管理好友」右侧抽屉（Bot 好友名单 PRD v1.3 R2 / §9.2）：
 * 按「好友用户 (N) / 好友 Bot (M)」两类 Tab 分栏，当前 Tab 内搜索，行内移除入口。
 * 移除经二次确认（复用移除好友确认弹窗语义、双 variant 文案 + 通知行），确认后行即时消失。
 */
import { Avatar } from '@/components/ui/Avatar';
import { Button } from '@/components/ui/Button';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import { Drawer, DrawerContent, DrawerDescription, DrawerHeader, DrawerTitle } from '@/components/ui/Drawer';
import { Empty } from '@/components/ui/Empty';
import { Input } from '@/components/ui/Input';
import { Segmented } from '@/components/ui/Segmented';
import {
  countRoster,
  filterRosterByKeyword,
  partitionRoster,
  pickDefaultRosterTab,
  type RosterActorType,
  type RosterFriend,
} from '@/domain/collaborationPrivacy/friendRoster';
import { useEffect, useMemo, useRef, useState } from 'react';

const TAB_LABEL: Record<RosterActorType, string> = { human: '好友用户', bot: '好友 Bot' };
const EMPTY_LABEL: Record<RosterActorType, string> = { human: '暂无好友用户', bot: '暂无好友 Bot' };

export interface FriendRosterDrawerProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  botName: string;
  friends: RosterFriend[];
  loading: boolean;
  error: string | null;
  onRetry: () => void;
  onRemove: (friend: RosterFriend) => void;
}

/** 移除确认文案：双 variant（好友用户 / 好友 Bot 通知其 Owner），后果分行 + 通知行（PRD R8）。 */
function removalDescription(friend: RosterFriend) {
  const isHuman = friend.type === 'human';
  return (
    <>
      <span className="block">
        确定移除好友 <strong>{friend.displayName}</strong> 吗？
      </span>
      <span className="mt-2 block">· 移除后如需继续协作，须重新发起添加好友申请</span>
      <span className="mt-1 block">· 历史单聊会话将从会话列表隐藏，重新添加好友后恢复</span>
      <span className="mt-1 block">{isHuman ? '· 移除后将通知该用户。' : '· 移除后将通知该 Bot 的 Owner。'}</span>
    </>
  );
}

export function FriendRosterDrawer({
  open,
  onOpenChange,
  botName,
  friends,
  loading,
  error,
  onRetry,
  onRemove,
}: FriendRosterDrawerProps) {
  const counts = useMemo(() => countRoster(friends), [friends]);
  const [tab, setTab] = useState<RosterActorType>(() => pickDefaultRosterTab(counts));
  const [keyword, setKeyword] = useState('');
  const [removalTarget, setRemovalTarget] = useState<RosterFriend | null>(null);
  // 用户手动切过 Tab 后不再被「默认首个有内容的 Tab」覆盖（PRD R2）。
  const userPickedTabRef = useRef(false);

  // 打开时重置交互态；关闭时清空选择与搜索，下次打开回到默认态。
  useEffect(() => {
    if (!open) {
      setRemovalTarget(null);
      setKeyword('');
      userPickedTabRef.current = false;
    }
  }, [open]);

  // 数据异步到达后：当前 Tab 为空且另一类有内容时，切到首个有内容的 Tab（好友用户优先）。
  useEffect(() => {
    if (!open || userPickedTabRef.current) return;
    if (counts[tab] === 0 && counts.total > 0) {
      setTab(pickDefaultRosterTab(counts));
    }
  }, [open, counts, tab]);

  const options = useMemo(
    () => [
      { value: 'human' as const, label: `${TAB_LABEL.human} (${counts.human})` },
      { value: 'bot' as const, label: `${TAB_LABEL.bot} (${counts.bot})` },
    ],
    [counts],
  );

  const visible = useMemo(
    () => filterRosterByKeyword(partitionRoster(friends, tab), keyword),
    [friends, tab, keyword],
  );

  const handleTabChange = (next: RosterActorType) => {
    userPickedTabRef.current = true;
    setTab(next);
  };

  const handleConfirmRemove = () => {
    if (removalTarget) {
      onRemove(removalTarget);
      setRemovalTarget(null);
    }
  };

  return (
    <Drawer open={open} onOpenChange={onOpenChange}>
      <DrawerContent side="right" size="md">
        <DrawerHeader>
          <DrawerTitle>{botName}的好友名单</DrawerTitle>
          <DrawerDescription>查看并管理该 Bot 的好友用户与好友 Bot。</DrawerDescription>
        </DrawerHeader>

        <Segmented<RosterActorType>
          value={tab}
          options={options}
          onChange={handleTabChange}
          aria-label="好友类型"
          className="w-full"
        />

        <div className="mt-3 mb-3">
          <Input
            value={keyword}
            onChange={(event) => setKeyword(event.target.value)}
            placeholder="搜索好友"
            aria-label="搜索好友"
          />
        </div>

        {loading ? (
          <p className="py-8 text-center text-sm text-muted-foreground">加载中…</p>
        ) : error ? (
          <Empty
            title="好友名单加载失败"
            description={error}
            action={
              <Button variant="secondary" onClick={onRetry}>
                重试
              </Button>
            }
            size="sm"
          />
        ) : visible.length === 0 ? (
          <Empty title={keyword.trim() ? '未找到匹配的好友' : EMPTY_LABEL[tab]} compact size="sm" />
        ) : (
          <ul className="m-0 flex list-none flex-col gap-1 p-0">
            {visible.map((friend) => (
              <li
                key={`${friend.type}:${friend.id}`}
                className="flex items-center gap-3 rounded-lg px-2 py-2 hover:bg-muted/60"
              >
                <Avatar name={friend.displayName} size={32} />
                <span className="min-w-0 flex-1 truncate text-sm text-foreground">{friend.displayName}</span>
                <Button variant="ghost" onClick={() => setRemovalTarget(friend)} aria-label={`移除 ${friend.displayName}`}>
                  移除
                </Button>
              </li>
            ))}
          </ul>
        )}

        <ConfirmDialog
          open={Boolean(removalTarget)}
          title="移除好友"
          description={removalTarget ? removalDescription(removalTarget) : undefined}
          confirmText="确认移除"
          confirmVariant="destructive"
          onCancel={() => setRemovalTarget(null)}
          onConfirm={handleConfirmRemove}
        />
      </DrawerContent>
    </Drawer>
  );
}