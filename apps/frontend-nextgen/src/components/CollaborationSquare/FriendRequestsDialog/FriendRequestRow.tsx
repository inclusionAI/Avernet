import type { HumanIdentity } from '@/capabilities';
import { Avatar } from '@/components/ui/Avatar';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import type { FriendRequestDecision, FriendRequestDirection, FriendRequestItem } from '@/domain/collaborationSquare/friendRequests';
import { sameFriendActor } from '@/domain/collaborationSquare/friendRequests';
import type { FriendRequestActor } from '@/domain/collaborationSquare/types';

interface FriendRequestRowProps {
  item: FriendRequestItem;
  direction: FriendRequestDirection;
  actor: FriendRequestActor;
  human: HumanIdentity;
  busy: boolean;
  error?: string;
  onDecide: (requestId: string, decision: FriendRequestDecision) => Promise<void>;
}
const statusLabels = { pending: '待处理', approved: '已通过', rejected: '已拒绝', cancelled: '已取消' };

export function FriendRequestRow({ item, direction, actor, human, busy, error, onDecide }: FriendRequestRowProps) {
  const party = direction === 'sent' ? item.toActor : item.fromActor;
  const isCurrentHuman = party.type === 'human' && party.id === human.userId;
  const name = isCurrentHuman ? human.displayName || human.userId : party.name;
  const avatarUrl = isCurrentHuman ? human.avatarUrl : party.avatarUrl;
  const canDecide = direction === 'received' && item.status === 'pending' && sameFriendActor(item.toActor, actor);
  return (
    <li className="space-y-3 rounded-lg border border-border bg-card p-4">
      <div className="flex flex-wrap items-start gap-3">
        <Avatar key={avatarUrl || party.id} name={name} src={avatarUrl} size={40} />
        <div className="min-w-0 flex-1 space-y-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="break-all text-sm font-medium">{name}</span>
            <Badge tone="neutral">{party.type === 'bot' ? 'Bot' : 'Human'}</Badge>
            {party.online !== undefined ? <span className="text-xs text-muted-foreground">{party.online ? '在线' : '离线'}</span> : null}
          </div>
          <p className="break-all text-xs text-muted-foreground">{party.id}</p>
        </div>
        <Badge tone={item.status === 'approved' ? 'success' : 'neutral'}>{statusLabels[item.status]}</Badge>
      </div>
      {item.message ? <p className="whitespace-pre-wrap break-words text-sm text-muted-foreground">{item.message}</p> : null}
      {item.decisionReason ? <p className="break-words text-xs text-muted-foreground">处理说明：{item.decisionReason}</p> : null}
      {canDecide ? (
        <div className="flex justify-end gap-2">
          <Button size="sm" variant="secondary" disabled={busy} onClick={() => void onDecide(item.requestId, 'reject')}>拒绝</Button>
          <Button size="sm" disabled={busy} onClick={() => void onDecide(item.requestId, 'accept')}>通过</Button>
        </div>
      ) : null}
      {error ? <p role="alert" className="text-sm text-destructive">{error}</p> : null}
    </li>
  );
}
