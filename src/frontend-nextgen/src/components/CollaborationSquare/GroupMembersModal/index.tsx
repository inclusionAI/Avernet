import { Badge } from '@/components/ui/Badge';
import { Modal, ModalContent, ModalDescription, ModalHeader, ModalTitle } from '@/components/ui/Modal';
import { Skeleton } from '@/components/ui/Skeleton';
import type { PublicGroup, PublicGroupMember } from '@/domain/collaborationSquare/types';

interface GroupMembersModalProps {
  open: boolean;
  group: PublicGroup | null;
  members: PublicGroupMember[];
  loading: boolean;
  onClose: () => void;
}

/**
 * 角色 → 中文标签与色调，按群类型归一（与「对话协作」成员管理 card 一致）：
 * 自由聊天/自定义协同 driver→群主；任务协作 manager→主节点、worker→从节点；其余一律「成员」（默认蓝）。
 */
function getRoleBadge(role: string, typeLabel?: string): { label: string; tone: 'purple' | 'primary' } {
  if (typeLabel === '任务协作') {
    if (role === 'manager') return { label: '主节点', tone: 'purple' };
    if (role === 'worker') return { label: '从节点', tone: 'primary' };
    return { label: '成员', tone: 'primary' };
  }
  if (role === 'driver') return { label: '群主', tone: 'purple' };
  return { label: '成员', tone: 'primary' };
}

/** 与 MemberList 一致的首字母圆标头像：bot 主色软底、human brand 软底。 */
function MemberAvatar({ member }: { member: PublicGroupMember }) {
  const symbol = member.displayName?.trim().charAt(0) || '?';
  return (
    <div
      className={
        member.type === 'bot'
          ? 'grid size-7 flex-none place-items-center rounded-full bg-primary/10 text-xs font-semibold text-primary'
          : 'grid size-7 flex-none place-items-center rounded-full bg-brand/15 text-xs font-medium text-brand'
      }
    >
      {symbol}
    </div>
  );
}

export function GroupMembersModal({ open, group, members, loading, onClose }: GroupMembersModalProps) {
  return (
    <Modal open={open} onOpenChange={(next) => !next && onClose()}>
      <ModalContent>
        <ModalHeader>
          <ModalTitle>{group ? `${group.name} · 公开成员` : '公开成员'}</ModalTitle>
          <ModalDescription>成员详情仅展示名称、身份类型和角色。</ModalDescription>
        </ModalHeader>
        {loading ? (
          <div aria-label="正在加载成员">
            <Skeleton.ListItem />
            <Skeleton.ListItem />
          </div>
        ) : members.length === 0 ? (
          <p className="m-0 text-sm leading-6 text-muted-foreground">暂无成员信息。</p>
        ) : (
          <div className="max-h-[400px] overflow-y-auto rounded-lg border border-border">
            {members.map((member, index) => {
              const roleBadge = getRoleBadge(member.role, group?.typeLabel);
              const isLast = index === members.length - 1;
              return (
                <div
                  key={member.id}
                  className={`flex items-center gap-3 px-4 py-3 transition-colors hover:bg-muted/50${
                    isLast ? '' : ' border-b border-border'
                  }`}
                >
                  <MemberAvatar member={member} />
                  <span className="min-w-0 flex-1 truncate text-sm font-semibold text-foreground">
                    {member.displayName}
                  </span>
                  <div className="flex shrink-0 items-center gap-1.5">
                    <Badge tone="primary">{member.type === 'bot' ? 'Bot' : '用户'}</Badge>
                    <Badge tone={roleBadge.tone}>{roleBadge.label}</Badge>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </ModalContent>
    </Modal>
  );
}
