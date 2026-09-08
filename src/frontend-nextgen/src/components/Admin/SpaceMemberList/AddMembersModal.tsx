// 添加成员多选模态：搜索员工 → 多选 chip 集（选中不清空、可逐个移除）→ 共享角色 → 批量提交。
// 下拉对 已是成员/自己/本批次已选 三类置灰去重。部分失败时把 failed 子集回填为 chip 集，
// 模态保持打开供重试；全部成功则清选 + 复位 + 关闭。成功者由 hook 经 refreshMembers 拉进列表。
import type { SearchedUser } from '@/capabilities';
import {
  Button,
  CaptionText,
  Modal,
  ModalContent,
  ModalFooter,
  ModalHeader,
  ModalTitle,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  ValueText,
} from '@/components/ui';
import { Card } from '@/components/ui/Card';
import type { SpaceMember } from '@/domain/admin/models';
import { X } from 'lucide-react';
import { useMemo, useState } from 'react';
import { UserSearchDropdown } from './UserSearchDropdown';

export interface AddMembersModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** 当前已是成员列表（下拉中置灰「已添加」）。 */
  members: SpaceMember[];
  /** 当前操作者 userId（下拉置灰，避免加自己）；null=未取得身份。 */
  currentUid?: string | null;
  /** 批量添加：多选 chip + 共享角色，返回 {succeeded, failed} 供本模态做部分失败重试回填。 */
  onAddMembers: (
    users: SearchedUser[],
    role: 'ADMIN' | 'MEMBER',
  ) => void | Promise<{ succeeded: SpaceMember[]; failed: { userId: string; userName?: string; reason: string }[] } | undefined>;
  addMembersLoading?: boolean;
  addMembersDisabledReason?: string;
}

export function AddMembersModal({
  open,
  onOpenChange,
  members,
  currentUid,
  onAddMembers,
  addMembersLoading = false,
  addMembersDisabledReason,
}: AddMembersModalProps) {
  const [selectedUsers, setSelectedUsers] = useState<SearchedUser[]>([]);
  const [newRole, setNewRole] = useState<'ADMIN' | 'MEMBER'>('MEMBER');

  // 已是成员 ∪ 自己 ∪ 本批次已选 → 下拉置灰，避免重复添加 / 加自己 / 重选
  const disabledUserIds = useMemo(() => {
    const set = new Set<string>(members.map((m) => m.userId));
    if (currentUid) set.add(currentUid);
    selectedUsers.forEach((u) => set.add(u.userId));
    return set;
  }, [members, currentUid, selectedUsers]);

  const removeChip = (userId: string) => {
    setSelectedUsers((prev) => prev.filter((u) => u.userId !== userId));
  };

  // 批量提交：部分失败回填 failed 子集（模态保持打开供重试）；全成功清选 + 复位 + 关闭。
  const submitAdd = async () => {
    if (selectedUsers.length === 0 || addMembersLoading) return;
    const result = await onAddMembers(selectedUsers, newRole);
    if (result && result.failed.length > 0) {
      // 把失败项还原为可重试的 chip（以 userName 作 nickName，保持展示一致）
      setSelectedUsers(
        result.failed.map((f) => ({ userId: f.userId, displayName: f.userName ?? f.userId, nickName: f.userName })),
      );
      return;
    }
    setSelectedUsers([]);
    setNewRole('MEMBER');
    onOpenChange(false);
  };

  return (
    <Modal open={open} onOpenChange={onOpenChange}>
      <ModalContent size="sm" className="max-w-[420px]">
        <ModalHeader>
          <ModalTitle>添加成员</ModalTitle>
        </ModalHeader>
        <div className="space-y-4 py-2">
          <div className="space-y-2">
            <CaptionText as="label">搜索员工</CaptionText>
            {/* 多选：选中不清空，追加到 chip 集；下拉对 已成员/自己/本批次已选 三类置灰去重 */}
            <UserSearchDropdown
              disabledUserIds={disabledUserIds}
              onSelect={(u) =>
                setSelectedUsers((prev) => (prev.some((p) => p.userId === u.userId) ? prev : [...prev, u]))
              }
              disabled={addMembersLoading}
            />
            {selectedUsers.length > 0 && (
              <ul className="m-0 flex flex-wrap gap-2 list-none p-0">
                {selectedUsers.map((u) => (
                  <li key={u.userId}>
                    <Card className="flex items-center gap-2 rounded-full bg-muted/30 px-2.5 py-1 shadow-sm">
                      <span className="inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-primary text-[10px] font-medium text-primary-foreground">
                        {(u.nickName || u.realName || u.userId || '?').charAt(0).toUpperCase()}
                      </span>
                      <ValueText as="span" className="min-w-0 truncate text-xs">
                        {u.nickName ? `${u.nickName}(${u.userId})` : u.userId}
                      </ValueText>
                      <Button
                        variant="ghost"
                        size="icon"
                        aria-label="取消选择"
                        className="h-5 w-5 text-muted-foreground"
                        onClick={() => removeChip(u.userId)}
                      >
                        <X size={12} />
                      </Button>
                    </Card>
                  </li>
                ))}
              </ul>
            )}
          </div>
          <div className="space-y-2">
            <CaptionText as="label">分配角色</CaptionText>
            <Select value={newRole} onValueChange={(v) => setNewRole(v as 'ADMIN' | 'MEMBER')}>
              <SelectTrigger className="h-9 w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="MEMBER">成员</SelectItem>
                <SelectItem value="ADMIN">管理员</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </div>
        <ModalFooter>
          <Button variant="ghost" size="sm" onClick={() => onOpenChange(false)} disabled={addMembersLoading}>
            取消
          </Button>
          <Button
            variant="primary"
            size="sm"
            onClick={() => void submitAdd()}
            disabled={selectedUsers.length === 0 || addMembersLoading}
            title={addMembersDisabledReason}
          >
            {addMembersLoading ? '添加中…' : `添加 ${selectedUsers.length} 人`}
          </Button>
        </ModalFooter>
      </ModalContent>
    </Modal>
  );
}

export default AddMembersModal;
