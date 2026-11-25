/**
 * 好友名单管理抽屉（公开设置 · Owner)：两页签全量列表（fetch-all,无截断）+
 * 客户端搜索（展示名/原始 id)+ 逐行二次确认移除。数据面全部来自
 * useBotFriendRoster(section 只负责呈现/交互);详情缺失的好友按原始 id 渲染
 * 且仍可移除（悬挂边不静默丢弃）。
 */
import { filterFriendViewsByKeyword } from '@/domain/collaborationPrivacy/botFriends';
import { Avatar } from '@/components/ui/Avatar';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import {
  Drawer,
  DrawerContent,
  DrawerHeader,
  DrawerTitle,
} from '@/components/ui/Drawer';
import { Empty } from '@/components/ui/Empty';
import { Input } from '@/components/ui/Input';
import { Segmented } from '@/components/ui/Segmented';
import type { BotFriendRoster } from '@/hooks/useBotFriendRoster';
import type {
  BotFriendActorType,
  BotIdentityFriendView,
} from '@/services/workspace/botFriendConversationService';
import { RefreshCw, Search, UsersRound } from 'lucide-react';
import { useState } from 'react';
import { toast } from 'sonner';

const TAB_LABEL: Record<BotFriendActorType, string> = {
  human: '好友用户',
  bot: '好友 Bot',
};

interface BotFriendManageDrawerProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  roster: BotFriendRoster;
}

function removalConfirmCopy(view: BotIdentityFriendView): string {
  return view.actorType === 'bot'
    ? `移除后双方好友关系将同步解除，该 Bot 的 Owner 将在通知中心收到系统消息。确定移除好友 Bot「${view.displayName}」吗？`
    : `移除后双方好友关系将同步解除，对方将在通知中心收到系统消息。确定移除好友「${view.displayName}」吗？`;
}

function FriendRow({
  view,
  removing,
  onRemove,
}: {
  view: BotIdentityFriendView;
  removing: boolean;
  onRemove: () => void;
}) {
  return (
    <div className="flex items-center gap-3 border-b border-divider px-1 py-3 last:border-b-0">
      <Avatar name={view.displayName} size={32} />
      <div className="min-w-0 flex-1">
        <p className="m-0 truncate text-sm font-medium text-foreground">{view.displayName}</p>
        <p className="m-0 mt-0.5 flex items-center gap-1.5 text-xs text-muted-foreground">
          {view.actorType === 'bot' ? (
            <>
              <Badge tone={view.online ? 'success' : 'neutral'}>{view.online ? '在线' : '离线'}</Badge>
              <span className="truncate">{view.actorId}</span>
            </>
          ) : (
            <span className="truncate">工号 {view.actorId}</span>
          )}
        </p>
      </div>
      <Button variant="ghost" size="sm" disabled={removing} onClick={onRemove}>
        移除
      </Button>
    </div>
  );
}

export function BotFriendManageDrawer({ open, onOpenChange, roster }: BotFriendManageDrawerProps) {
  const [tab, setTab] = useState<BotFriendActorType>('human');
  const [keyword, setKeyword] = useState('');
  const [confirming, setConfirming] = useState<BotIdentityFriendView | null>(null);
  const [confirmingBusy, setConfirmingBusy] = useState(false);

  const section = roster.sections?.[tab];
  const items = section?.ok ? filterFriendViewsByKeyword(section.data.items, keyword) : [];

  const handleConfirm = async () => {
    if (!confirming) return;
    setConfirmingBusy(true);
    const removal = await roster.removeFriend(confirming);
    setConfirmingBusy(false);
    if (removal.duplicate) return;
    if (!removal.ok) {
      // 失败态由抽屉内的 removeError 行呈现;确认框收起不再误报 toast-only。
      setConfirming(null);
      return;
    }
    if (removal.alreadyRemoved) {
      toast.warning(`「${confirming.displayName}」已不在好友名单中`);
    } else {
      toast.success(`已移除好友「${confirming.displayName}」`);
    }
    setConfirming(null);
  };

  return (
    <Drawer open={open} onOpenChange={onOpenChange}>
      <DrawerContent side="right" size="md" bodyClassName="flex flex-col gap-4">
        <DrawerHeader>
          <DrawerTitle>管理好友</DrawerTitle>
        </DrawerHeader>
        <Segmented<BotFriendActorType>
          aria-label="好友类型"
          value={tab}
          options={[
            { value: 'human', label: TAB_LABEL.human },
            { value: 'bot', label: TAB_LABEL.bot },
          ]}
          onChange={(next) => {
            setTab(next);
            setKeyword('');
          }}
        />
        <Input
          placeholder="搜索好友"
          value={keyword}
          onChange={(event) => setKeyword(event.target.value)}
        />
        {roster.removeError ? (
          <p className="m-0 text-xs text-destructive">{roster.removeError.friendlyMessage}</p>
        ) : null}
        <div className="min-h-0 flex-1">
          {!section ? (
            <Empty icon={<UsersRound className="h-5 w-5" aria-hidden />} title="好友名单加载中" />
          ) : !section.ok ? (
            <Empty
              title={`加载${TAB_LABEL[tab]}失败`}
              description={section.error.friendlyMessage}
              icon={<RefreshCw className="h-5 w-5" aria-hidden />}
              action={
                <Button
                  leftIcon={<RefreshCw className="h-4 w-4" aria-hidden />}
                  onClick={() => void roster.reload(tab)}
                >
                  重试
                </Button>
              }
            />
          ) : items.length === 0 ? (
            <Empty
              icon={<Search className="h-5 w-5" aria-hidden />}
              title={keyword.trim() ? '没有匹配的好友' : `暂无${TAB_LABEL[tab]}`}
            />
          ) : (
            <div>
              {items.map((view) => (
                <FriendRow
                  key={view.queryId}
                  view={view}
                  removing={roster.removingIds.includes(view.actorId)}
                  onRemove={() => setConfirming(view)}
                />
              ))}
            </div>
          )}
        </div>
        <ConfirmDialog
          open={Boolean(confirming)}
          title="移除好友"
          description={confirming ? removalConfirmCopy(confirming) : ''}
          confirmText="确定移除"
          cancelText="取消"
          confirmVariant="destructive"
          loading={confirmingBusy || Boolean(confirming && roster.removingIds.includes(confirming.actorId))}
          onCancel={() => setConfirming(null)}
          onConfirm={handleConfirm}
        />
      </DrawerContent>
    </Drawer>
  );
}
