import { Badge, Button, IconButton, Skeleton } from '@/components/ui';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import type { GroupView, IdentityView, ParticipantView } from '@/domain/collaboration';
import { resolveAuthenticatedDisplayName } from '@/domain/userIdentity';
import { Plus, Trash2 } from 'lucide-react';
import { useState } from 'react';
import { AddMemberDialog } from './AddMemberDialog';

export interface MemberListProps {
  participants: ParticipantView[];
  /** 后端已知成员数 >0 但 participants 尚未拉回时显示加载骨架。 */
  participantCount?: number;
  loading?: boolean;
  activeIdentity?: IdentityView | null;
  authenticatedUserId?: string | null;
  authenticatedUserName?: string | null;
  canManage: boolean;
  disabledReason?: string;
  emptyText?: string;
  addLabel?: string;
  groupKind: GroupView['kind'];
  showMode?: boolean;
  onAddMany: (actorIds: string[]) => Promise<number>;
  onRemove: (actorId: string) => Promise<boolean>;
}

/** 角色标签：自由聊天/自定义协同 driver→群主；任务协作 manager→主节点、worker→从节点；其余一律「成员」。 */
function getRoleLabel(participant: ParticipantView, groupKind: GroupView['kind']): string {
  if (groupKind === 'task_master_slave') {
    if (participant.role === 'manager') return '主节点';
    if (participant.role === 'worker') return '从节点';
    return '成员';
  }
  return participant.role === 'driver' ? '群主' : '成员';
}

function getModeLabel(participant: ParticipantView): string | null {
  if (participant.kind === 'bot') {
    if (participant.mode === 'auto') return '自动';
    if (participant.mode === 'muted') return '禁言';
    return null;
  }
  if (participant.mode === 'present') return '参与';
  if (participant.mode === 'absent') return '旁观';
  return null;
}

/** 角色标签色：群主/主节点（管理角色）紫；从节点/成员走默认蓝。 */
function getRoleTone(participant: ParticipantView, groupKind: GroupView['kind']): 'purple' | 'primary' {
  if (groupKind === 'task_master_slave') return participant.role === 'manager' ? 'purple' : 'primary';
  return participant.role === 'driver' ? 'purple' : 'primary';
}

/** 状态标签色：自动/参与绿，禁言/旁观灰。 */
function getModeTone(participant: ParticipantView): 'success' | 'neutral' {
  if (participant.kind === 'bot') return participant.mode === 'auto' ? 'success' : 'neutral';
  return participant.mode === 'present' ? 'success' : 'neutral';
}

function Avatar({ participant }: { participant: ParticipantView }) {
  const symbol = participant.name?.trim().charAt(0) || '?';
  return (
    <div
      className={
        participant.kind === 'bot'
          ? // bot 图标对齐 Bot 工坊信息列首字母圆标：主色软底（bg-primary/10 + text-primary）。
            'grid size-8 flex-none place-items-center rounded-full bg-primary/10 text-xs font-semibold text-primary shadow-sm'
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
  authenticatedUserId,
  authenticatedUserName,
  canManage,
  disabledReason,
  emptyText = '暂无成员',
  addLabel = '添加成员',
  groupKind,
  showMode = false,
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
          {participants.map((participant) => {
            const displayName = resolveAuthenticatedDisplayName(
              { id: participant.actorId, kind: participant.kind, name: participant.name },
              authenticatedUserId ? { userId: authenticatedUserId, name: authenticatedUserName } : null,
            );
            return (
              <div
                key={participant.actorId}
                data-testid={`member-${participant.actorId}`}
                className="flex items-center gap-2 rounded-lg border border-border bg-card p-2 shadow-sm"
              >
                <Avatar participant={{ ...participant, name: displayName }} />
                <div className="min-w-0 flex-1">
                  <div className="flex items-center">
                    <span className="max-w-full truncate text-sm font-semibold text-foreground">{displayName}</span>
                  </div>
                  {/* 标签分类型着色（Badge 默认字号）：成员类型默认蓝；群主/主节点紫；自动/参与绿；禁言/旁观灰。 */}
                  <div className="mt-1 flex flex-wrap items-center gap-1.5">
                    <Badge tone="primary">{participant.kind === 'bot' ? 'Bot' : '用户'}</Badge>
                    <Badge tone={getRoleTone(participant, groupKind)}>{getRoleLabel(participant, groupKind)}</Badge>
                    {showMode && getModeLabel(participant) ? (
                      <Badge tone={getModeTone(participant)}>{getModeLabel(participant)}</Badge>
                    ) : null}
                  </div>
                </div>
                {canManage && participant.role !== 'owner' && (
                  <ConfirmDialog
                    title={`移除成员 ${displayName}`}
                    description="移除后将无法参与当前协作。"
                    confirmText="确认移除"
                    confirmVariant="destructive"
                    onConfirm={() => void onRemove(participant.actorId)}
                  >
                    <IconButton
                      label="移除成员"
                      icon={<Trash2 className="h-4 w-4" aria-hidden />}
                      size="sm"
                      className="shrink-0 text-destructive hover:bg-destructive/10 hover:text-destructive"
                    />
                  </ConfirmDialog>
                )}
              </div>
            );
          })}
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
