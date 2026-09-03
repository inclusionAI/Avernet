import { Badge, IconButton, Segmented } from '@/components/ui';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import type { GroupView, ParticipantMode, ParticipantRole, ParticipantView, SessionView } from '@/domain/collaboration';
import { cn } from '@/utils/cn';
import { UserMinus, X } from 'lucide-react';

export interface MembersPanelProps {
  group: GroupView;
  session: SessionView | null;
  canManage: { allowed: boolean; disabledReason?: string };
  onUpdateMode: (actorId: string, mode: ParticipantMode) => void;
  onRemoveParticipant: (actorId: string) => void;
  onClose: () => void;
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

const MODE_OPTIONS: { value: ParticipantMode; label: string }[] = [
  { value: 'auto', label: '自动' },
  { value: 'present', label: '在场' },
  { value: 'muted', label: '静音' },
  { value: 'absent', label: '离开' },
];

function Avatar({ participant }: { participant: ParticipantView }) {
  const symbol = participant.name?.trim().charAt(0) || '?';
  if (participant.kind === 'bot') {
    return (
      <div className="grid size-8 flex-none place-items-center rounded-full bg-zinc-900 text-xs font-medium text-primary-foreground dark:bg-zinc-100 dark:text-zinc-900">
        {symbol}
      </div>
    );
  }
  return (
    <div className="grid size-8 flex-none place-items-center rounded-full bg-brand/15 text-xs font-medium text-brand">
      {symbol}
    </div>
  );
}

function ParticipantRow({
  participant,
  canManage,
  onUpdateMode,
  onRemoveParticipant,
}: {
  participant: ParticipantView;
  canManage: { allowed: boolean; disabledReason?: string };
  onUpdateMode: (actorId: string, mode: ParticipantMode) => void;
  onRemoveParticipant: (actorId: string) => void;
}) {
  const isOwner = participant.role === 'owner';
  const options = MODE_OPTIONS.map((opt) => ({
    ...opt,
    disabledReason: canManage.allowed ? undefined : canManage.disabledReason,
  }));

  return (
    <div data-row={participant.actorId} className="flex items-center gap-2 px-3 py-2">
      <Avatar participant={participant} />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="truncate text-sm font-medium text-foreground">{participant.name}</span>
          <Badge tone={ROLE_BADGE_TONE[participant.role]}>{ROLE_LABEL[participant.role]}</Badge>
        </div>
      </div>
      <div className="flex-none">
        <Segmented<ParticipantMode>
          value={participant.mode}
          options={options}
          onChange={(mode) => onUpdateMode(participant.actorId, mode)}
        />
      </div>
      {canManage.allowed && !isOwner && (
        <ConfirmDialog
          title={`移除成员 ${participant.name ?? ''}`.trim()}
          description="移除后将无法参与当前协作群"
          confirmText="确认移除"
          cancelText="取消"
          confirmVariant="destructive"
          onConfirm={() => onRemoveParticipant(participant.actorId)}
        >
          <IconButton label="移除成员" icon={<UserMinus className="h-4 w-4" aria-hidden />} size="sm" variant="ghost" />
        </ConfirmDialog>
      )}
    </div>
  );
}

export function MembersPanel({
  group,
  session,
  canManage,
  onUpdateMode,
  onRemoveParticipant,
  onClose,
}: MembersPanelProps) {
  const groupParticipants = group.participants ?? [];
  const sessionParticipants = session?.participants ?? [];

  // Merge: prefer sessionParticipant mode (live), fallback to group participant.
  const sessionByActorId = new Map(sessionParticipants.map((p) => [p.actorId, p]));
  const renderedParticipants = groupParticipants.map((p) => sessionByActorId.get(p.actorId) ?? p);

  return (
    <aside className={cn('flex h-full flex-col bg-background')}>
      <header className="flex items-center justify-between border-b border-border px-3 py-2.5">
        <div className="min-w-0">
          <h2 className="m-0 truncate text-sm font-semibold text-foreground">成员管理</h2>
          <p className="m-0 mt-0.5 truncate text-xs text-muted-foreground">
            {group.name} · {renderedParticipants.length} 个成员
          </p>
        </div>
        <IconButton
          label="关闭成员面板"
          icon={<X className="h-4 w-4" aria-hidden />}
          size="sm"
          variant="ghost"
          onClick={onClose}
        />
      </header>

      <div className="flex-1 overflow-y-auto">
        {renderedParticipants.length === 0 ? (
          <p className="px-4 py-6 text-center text-sm text-muted-foreground">暂无成员</p>
        ) : (
          <div className="divide-y divide-border">
            {renderedParticipants.map((p) => (
              <ParticipantRow
                key={p.actorId}
                participant={p}
                canManage={canManage}
                onUpdateMode={onUpdateMode}
                onRemoveParticipant={onRemoveParticipant}
              />
            ))}
          </div>
        )}
      </div>
    </aside>
  );
}

export default MembersPanel;
