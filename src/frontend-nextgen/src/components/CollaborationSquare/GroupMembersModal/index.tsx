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
          <div className="space-y-2">
            {members.map((member) => (
              <div
                key={member.id}
                className="flex items-center justify-between gap-3 rounded-lg border border-border p-3"
              >
                <div className="min-w-0">
                  <p className="m-0 truncate text-sm font-medium text-foreground">{member.displayName}</p>
                  <p className="m-0 mt-1 text-xs text-muted-foreground">
                    {member.type === 'human' ? '用户' : 'Bot'}
                  </p>
                </div>
                <Badge>{member.role}</Badge>
              </div>
            ))}
          </div>
        )}
      </ModalContent>
    </Modal>
  );
}
