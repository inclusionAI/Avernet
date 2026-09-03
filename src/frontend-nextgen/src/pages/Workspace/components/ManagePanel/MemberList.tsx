import { Badge, Button, Skeleton } from '@/components/ui';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import type { IdentityView, ParticipantRole, ParticipantView } from '@/domain/collaboration';
import { Plus, UserMinus } from 'lucide-react';
import { useState } from 'react';
import { AddMemberDialog } from './AddMemberDialog';

export interface MemberListProps {
  participants: ParticipantView[];
  /** 后端已知成员数 >0 但 participants 尚未拉回时显示加载骨架。 */
  participantCount?: number;
  loading?: boolean;
  activeIdentity?: IdentityView | null;
  canManage: boolean;
  disabledReason?: string;
  emptyText?: string;
  addLabel?: string;
  onAddMany: (actorIds: string[]) => Promise<number>;
  onRemove: (actorId: string) => Promise<boolean>;
}

const ROLE_LABEL: Record<ParticipantRole, string> = {
  owner: '群主',
  driver: '驱动',
  manager: '主节点',
  member: '成员',
};

const ROLE_BADGE_TONE: Record<ParticipantRole, 'primary' | 'neutral' | 'warning'> = {
  owner: 'primary',
  driver: 'warning',
  manager: 'warning',
  member: 'neutral',
};

function Avatar({ participant }: { participant: ParticipantView }) {
  const symbol = participant.name?.trim().charAt(0) || '?';
  return (
    <div
      className={
        participant.kind === 'bot'
          ? 'grid size-8 flex-none place-items-center rounded-full bg-foreground text-xs font-medium text-background shadow-sm'
          : 'grid size-8 flex-none place-items-center rounded-full bg-brand/15 text-xs font-medium text-brand shadow-sm'
      }
    >
      {symbol}
    </div>
  );
}

export function MemberList({
  participants,
  participantCount,
  loading,
  activeIdentity,
  canManage,
  disabledReason,
  emptyText = '暂无成员',
  addLabel = '添加成员',
  onAddMany,
  onRemove,
}: MemberListProps) {
  const [addOpen, setAddOpen] = useState(false);
  const existingIds = participants.map((p) => p.actorId);
  // participantCount >0 但 participants 为空 → 详情尚未拉回，显示加载骨架。
  const showLoading = loading || (participants.length === 0 && (participantCount ?? 0) > 0);

  return (
    <div>
      <div className="mb-2 flex items-center justify-between">
        <span className="text-xs font-medium text-muted-foreground">
          {showLoading ? '加载中…' : `${participants.length} 个成员`}
        </span>
        {canManage && (
          <Button
            variant="secondary"
            size="sm"
            leftIcon={<Plus className="h-3.5 w-3.5" />}
            onClick={() => setAddOpen(true)}
          >
            {addLabel}
          </Button>
        )}
      </div>

      {!canManage && disabledReason ? <p className="m-0 mb-2 text-xs text-muted-foreground">{disabledReason}</p> : null}

      {participants.length === 0 ? (
        showLoading ? (
          <div className="space-y-2">
            {[1, 2, 3].map((i) => (
              <Skeleton.Block key={i} className="h-12 w-full rounded-lg" />
            ))}
          </div>
        ) : (
          <div className="rounded-lg border border-dashed border-border px-3 py-4 text-center text-sm text-muted-foreground">
            {emptyText}
          </div>
        )
      ) : (
        <div className="space-y-2">
          {participants.map((participant) => (
            <div
              key={participant.actorId}
              className="flex items-center gap-2 rounded-lg border border-border bg-card p-2 shadow-sm"
            >
              <Avatar participant={participant} />
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-1.5">
                  <span className="max-w-full truncate text-sm font-semibold text-foreground">{participant.name}</span>
                  <Badge tone={participant.kind === 'bot' ? 'primary' : 'neutral'}>
                    {participant.kind === 'bot' ? 'Bot' : '用户'}
                  </Badge>
                  <Badge tone={ROLE_BADGE_TONE[participant.role]}>{ROLE_LABEL[participant.role]}</Badge>
                </div>
                <p className="m-0 mt-0.5 text-xs text-muted-foreground">
                  {participant.mode === 'present'
                    ? '在场'
                    : participant.mode === 'muted'
                    ? '静音'
                    : participant.mode === 'absent'
                    ? '离开'
                    : '自动'}
                </p>
              </div>
              {canManage && participant.role !== 'owner' && (
                <ConfirmDialog
                  title={`移除成员 ${participant.name}`}
                  description="移除后将无法参与当前协作。"
                  confirmText="确认移除"
                  confirmVariant="destructive"
                  onConfirm={() => void onRemove(participant.actorId)}
                >
                  <Button variant="ghost" size="sm" className="shrink-0 text-muted-foreground hover:text-destructive">
                    <UserMinus className="h-4 w-4" />
                    移除
                  </Button>
                </ConfirmDialog>
              )}
            </div>
          ))}
        </div>
      )}

      <AddMemberDialog
        open={addOpen}
        existingIds={existingIds}
        activeIdentity={activeIdentity}
        onClose={() => setAddOpen(false)}
        onAddMany={onAddMany}
      />
    </div>
  );
}
