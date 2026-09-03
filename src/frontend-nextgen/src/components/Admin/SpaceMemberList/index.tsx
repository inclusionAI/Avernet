// 空间成员表（对齐 PRD）：成员行列表 + 按空间状态差异化底部。
// - 已加入且可管理：顶部右侧「添加成员」按钮；角色可改/可删；无底部提示。
// - 未加入团队空间：顶部右侧「可申请」文字；最右下角「申请加入」按钮；最左下角提示文案(上方横线)。
// - 个人空间：无顶部按钮；最左下角提示文案(上方横线)。
// 单行渲染下沉到 SpaceMemberRow；角色变更直接执行（PRD 交互，无二次确认）。
import type { SearchedUser } from '@/capabilities';
import {
  Button,
  CaptionText,
  ConfirmDialog,
  Empty,
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
  Skeleton,
  TableHeaderText,
  ValueText,
} from '@/components/ui';
import { Card } from '@/components/ui/Card';
import type { Space, SpaceMember } from '@/domain/admin/models';
import { adminService } from '@/services/admin';
import { readUserId } from '@/services/admin/userIdentity';
import { Plus, User, Users, X } from 'lucide-react';
import { useMemo, useState } from 'react';
import { SpaceJoinForm } from '../SpaceJoinForm';
import { Tag } from '../Tag';
import { SpaceMemberRow } from './SpaceMemberRow';
import { UserSearchDropdown } from './UserSearchDropdown';

// clawweb=Avernet 暂无 DELETE /openapi/v1/bots/spaces/{id} 接口；UI 入口隐藏，后端补接口时翻 true。
const DELETE_SPACE_SUPPORTED = false;

export interface SpaceMemberListProps {
  space: Space;
  members: SpaceMember[];
  loading: boolean;
  onAddMember: (userId: string, role: 'ADMIN' | 'MEMBER', userName?: string) => void | Promise<void>;
  onRemoveMember: (userId: string) => void | Promise<void>;
  onUpdateRole: (userId: string, role: 'ADMIN' | 'MEMBER') => void | Promise<void>;
  onDeleteSpace?: (spaceId: number | string) => void | Promise<void>;
  onRequestJoin?: (space: Space, reason: string) => void | Promise<void>;
}

export function SpaceMemberList({
  space,
  members,
  loading,
  onAddMember,
  onRemoveMember,
  onUpdateRole,
  onDeleteSpace,
  onRequestJoin,
}: SpaceMemberListProps) {
  const isPersonal = space.spaceType === 'PERSONAL';
  // 可管理：后端回传 current_user_role=ADMIN 即可；若后端未回传，则从成员列表推断当前用户是否 ADMIN。
  const currentUid = readUserId();
  const isOwnerByMemberList = !!currentUid && members.some((m2) => m2.userId === currentUid && m2.role === 'ADMIN');
  const manageable = !isPersonal && (adminService.canManage(space).ok || isOwnerByMemberList);
  const isMember = space.currentUserRole === 'ADMIN' || space.currentUserRole === 'MEMBER';
  const joinableTeam = !isMember && space.spaceType === 'TEAM' && space.joinStatus !== 'APPLYING';
  const [addOpen, setAddOpen] = useState(false);
  const [joinOpen, setJoinOpen] = useState(false);
  const [selectedUser, setSelectedUser] = useState<SearchedUser | null>(null);
  const [newRole, setNewRole] = useState<'ADMIN' | 'MEMBER'>('MEMBER');

  const lastOwner = members.filter((m) => m.role === 'ADMIN').length <= 1;

  // 添加成员下拉中禁用已有成员 + 当前用户（避免重复添加 / 加自己）
  const disabledUserIds = useMemo(() => {
    const set = new Set<string>(members.map((m) => m.userId));
    if (currentUid) set.add(currentUid);
    return set;
  }, [members, currentUid]);

  const submitAdd = async () => {
    if (!selectedUser) return;
    const { userId } = selectedUser;
    // 花名随被加成员写入成员表：nickName 即花名；缺失时退到 displayName（仅当其非工号，避免把工号误写成花名）。
    const userName =
      selectedUser.nickName || (selectedUser.displayName !== userId ? selectedUser.displayName : undefined);
    await onAddMember(userId, newRole, userName);
    setSelectedUser(null);
    setNewRole('MEMBER');
    setAddOpen(false);
  };

  // 顶部右侧：已加入可管理→添加成员按钮；未加入团队→「可申请」文字；个人空间→空
  const topRight = manageable ? (
    <Button size="sm" variant="primary" leftIcon={<Plus size={14} />} onClick={() => setAddOpen(true)}>
      添加成员
    </Button>
  ) : joinableTeam ? (
    <CaptionText as="span">可申请</CaptionText>
  ) : null;

  // 底部行：未加入团队→左提示 + 右申请加入按钮；个人空间→左提示
  const bottomBar = joinableTeam ? (
    <div className="mt-auto flex items-center justify-between border-t border-border pt-3">
      <CaptionText className="m-0">您尚未加入此团队，可申请加入，待管理员审批通过后即可访问。</CaptionText>
      <Button size="sm" variant="primary" onClick={() => setJoinOpen(true)} className="shrink-0">
        申请加入
      </Button>
    </div>
  ) : isPersonal ? (
    <CaptionText className="mt-auto border-t border-border pt-3">个人空间仅含您本人，不可添加或移除成员。</CaptionText>
  ) : manageable && DELETE_SPACE_SUPPORTED ? (
    <div className="mt-auto flex items-center justify-end border-t border-border pt-3">
      <ConfirmDialog
        title="确认删除该团队空间？"
        description={`删除后「${space.spaceName}」及其资源将离线，且不可撤销`}
        confirmText="删除"
        confirmVariant="destructive"
        onConfirm={() => onDeleteSpace?.(space.spaceId)}
      >
        <Button size="sm" variant="destructive">
          删除团队
        </Button>
      </ConfirmDialog>
    </div>
  ) : null;

  return (
    <div className="flex flex-1 flex-col gap-3">
      {/* 顶部行：空间名（左）+ 状态化右侧（添加成员 / 可申请文字） */}
      {/* 标题行：空间类型图标 + 名称（个人带紫色 tag）+ 状态化右侧（对齐 PRD Drawer title） */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2.5">
          {isPersonal ? (
            <User size={20} className="text-muted-foreground" aria-hidden />
          ) : (
            <Users size={20} className="text-primary" aria-hidden />
          )}
          <span className="text-[14px] font-semibold leading-5 text-foreground">{space.spaceName}</span>
          {isPersonal && <Tag>个人</Tag>}
        </div>
        {topRight}
      </div>

      {/* 成员表 */}
      {/* 骨架对齐真实成员表(容器+表头+行,列宽一致,loading↔loaded 列位不跳) */}
      <Card className="overflow-hidden shadow-sm">
        {loading ? (
          <>
            <div className="grid grid-cols-[minmax(0,1fr)_160px_80px] border-b border-border bg-muted/50 px-4 py-2.5">
              <Skeleton.Block className="h-3 w-10" />
              <Skeleton.Block className="h-3 w-8" />
              <Skeleton.Block className="ml-auto h-3 w-8" />
            </div>
            <ul className="m-0 list-none divide-y divide-border p-0">
              {Array.from({ length: 4 }).map((_, i) => (
                <li key={i} className="grid grid-cols-[minmax(0,1fr)_160px_80px] items-center px-4 py-2.5">
                  <div className="flex items-center gap-2">
                    <Skeleton.Block className="h-7 w-7 shrink-0 rounded-full" />
                    <Skeleton.Line className="w-24" />
                  </div>
                  <Skeleton.Block className="h-7 w-16 rounded" />
                  <div className="flex justify-end">
                    <Skeleton.Block className="h-7 w-7 rounded" />
                  </div>
                </li>
              ))}
            </ul>
          </>
        ) : members.length === 0 ? (
          <div className="px-4 py-5">
            <Empty title="暂无成员" description={manageable ? '可添加成员来协同管理空间' : '该空间暂无成员'} compact />
          </div>
        ) : (
          <>
            <div className="grid grid-cols-[minmax(0,1fr)_160px_80px] border-b border-border bg-muted/50 px-4 py-2.5">
              <TableHeaderText as="span">成员</TableHeaderText>
              <TableHeaderText as="span">角色</TableHeaderText>
              <TableHeaderText as="span" className="text-right">
                操作
              </TableHeaderText>
            </div>
            <ul className="m-0 list-none divide-y divide-border p-0">
              {members.map((m) => (
                <SpaceMemberRow
                  key={m.userId}
                  space={space}
                  member={m}
                  manageable={manageable}
                  lastOwner={lastOwner}
                  onUpdateRole={onUpdateRole}
                  onRemoveMember={onRemoveMember}
                />
              ))}
            </ul>
          </>
        )}
      </Card>

      {/* 底部：状态化提示 + 申请加入按钮 */}
      {bottomBar}

      {/* 添加成员 Modal */}
      {addOpen && (
        <Modal open={addOpen} onOpenChange={setAddOpen}>
          <ModalContent size="sm" className="max-w-[420px]">
            <ModalHeader>
              <ModalTitle>添加成员</ModalTitle>
            </ModalHeader>
            <div className="space-y-4 py-2">
              <div className="space-y-2">
                <CaptionText as="label">搜索员工</CaptionText>
                <UserSearchDropdown
                  disabledUserIds={disabledUserIds}
                  onSelect={setSelectedUser}
                  disabled={!!selectedUser}
                />
                {selectedUser && (
                  <Card className="flex items-center gap-2 rounded-lg bg-muted/30 px-3 py-2 shadow-sm">
                    <span className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-primary text-xs font-medium text-primary-foreground">
                      {(selectedUser.nickName || selectedUser.realName || selectedUser.userId || '?')
                        .charAt(0)
                        .toUpperCase()}
                    </span>
                    <ValueText as="span" className="min-w-0 flex-1 truncate">
                      {selectedUser.nickName ? `${selectedUser.nickName}(${selectedUser.userId})` : selectedUser.userId}
                    </ValueText>
                    <Button
                      variant="ghost"
                      size="icon"
                      aria-label="取消选择"
                      className="h-6 w-6 text-muted-foreground"
                      onClick={() => setSelectedUser(null)}
                    >
                      <X size={14} />
                    </Button>
                  </Card>
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
              <Button variant="ghost" size="sm" onClick={() => setAddOpen(false)}>
                取消
              </Button>
              <Button variant="primary" size="sm" onClick={() => void submitAdd()} disabled={!selectedUser}>
                添加
              </Button>
            </ModalFooter>
          </ModalContent>
        </Modal>
      )}

      {/* 申请加入 Modal（未加入团队时） */}
      <SpaceJoinForm
        space={space}
        open={joinOpen}
        onOpenChange={setJoinOpen}
        onSubmit={async (reason) => {
          await onRequestJoin?.(space, reason);
          return true;
        }}
      />
    </div>
  );
}

export default SpaceMemberList;
