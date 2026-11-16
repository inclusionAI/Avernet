// 空间成员表（对齐 PRD）：成员行列表 + 按空间状态差异化底部。
// - 已加入且可管理：顶部右侧「添加成员」按钮；角色可改/可删；无底部提示。
// - 未加入团队空间：顶部右侧「可申请」文字；最右下角「申请加入」按钮；最左下角提示文案(上方横线)。
// - 个人空间：标题旁问号展示成员限制说明；无添加/移除成员入口。
// 单行渲染下沉到 SpaceMemberRow；添加成员多选模态下沉到 AddMembersModal；角色变更直接执行（PRD 交互，无二次确认）。
import type { SearchedUser } from '@/capabilities';
import {
  Button,
  CaptionText,
  ConfirmDialog,
  Empty,
  Skeleton,
  TableHeaderText,
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui';
import { Card } from '@/components/ui/Card';
import type { Space, SpaceMember } from '@/domain/admin/models';
import { adminService } from '@/services/admin';
import { readUserId } from '@/services/admin/userIdentity';
import { CircleHelp, Plus } from 'lucide-react';
import { useState } from 'react';
import { SpaceJoinForm } from '../SpaceJoinForm';
import { AddMembersModal } from './AddMembersModal';
import { SpaceMemberRow } from './SpaceMemberRow';

// clawweb=Avernet 暂无 DELETE /openapi/v1/bots/spaces/{id} 接口；UI 入口隐藏，后端补接口时翻 true。
const DELETE_SPACE_SUPPORTED = false;

export interface SpaceMemberListProps {
  space: Space;
  members: SpaceMember[];
  loading: boolean;
  /** 批量添加成员（多选 chip + 共享角色）。返回 {succeeded, failed} 供模态做部分失败重试回填；可省返回。 */
  onAddMembers: (
    users: SearchedUser[],
    role: 'ADMIN' | 'MEMBER',
  ) => void | Promise<
    { succeeded: SpaceMember[]; failed: { userId: string; userName?: string; reason: string }[] } | undefined
  >;
  /** 批量添加 in-flight 加载态（来自 hook）；用于按钮禁用 + 加载文案。 */
  addMembersLoading?: boolean;
  addMembersDisabledReason?: string;
  onRemoveMember: (userId: string) => void | Promise<void>;
  onUpdateRole: (userId: string, role: 'ADMIN' | 'MEMBER') => void | Promise<void>;
  onDeleteSpace?: (spaceId: number | string) => void | Promise<void>;
  onRequestJoin?: (space: Space, reason: string) => void | boolean | Promise<void | boolean>;
}

export function SpaceMemberList({
  space,
  members,
  loading,
  onAddMembers,
  addMembersLoading = false,
  addMembersDisabledReason,
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

  const columns = manageable
    ? 'grid-cols-[minmax(0,1.35fr)_minmax(84px,1fr)_minmax(64px,0.96fr)_80px]'
    : 'grid-cols-[minmax(0,1.35fr)_minmax(84px,1fr)_minmax(64px,0.96fr)]';
  const lastOwner = members.filter((m) => m.role === 'ADMIN').length <= 1;

  // 顶部右侧：已加入可管理→添加成员按钮；未加入团队→「可申请」文字；个人空间→空
  const topRight = manageable ? (
    <Button
      size="sm"
      variant="primary"
      className="h-8 shrink-0 text-sm"
      leftIcon={<Plus size={14} />}
      onClick={() => setAddOpen(true)}
    >
      添加成员
    </Button>
  ) : joinableTeam ? (
    <CaptionText as="span">可申请</CaptionText>
  ) : null;

  // 底部行：未加入团队→左提示 + 右申请加入按钮；个人空间说明改为标题问号提示。
  const bottomBar = joinableTeam ? (
    <div className="mt-auto flex items-center justify-between border-t border-divider pt-3">
      <CaptionText className="m-0">您尚未加入此团队，可申请加入，待管理员审批通过后即可访问。</CaptionText>
      <Button size="sm" variant="primary" onClick={() => setJoinOpen(true)} className="shrink-0">
        申请加入
      </Button>
    </div>
  ) : manageable && DELETE_SPACE_SUPPORTED ? (
    <div className="mt-auto flex items-center justify-end border-t border-divider pt-3">
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
    <div className="flex min-h-full flex-1 flex-col">
      <div className="flex h-14 shrink-0 items-center justify-between gap-3 border-b border-border pl-5 pr-14">
        <div className="flex min-w-0 items-center gap-1">
          <h2 className="min-w-0 truncate text-base font-medium leading-6" title={space.spaceName}>
            {space.spaceName}
          </h2>
          {isPersonal && (
            <TooltipProvider delayDuration={200}>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button variant="ghost" size="icon" className="size-4 rounded-full p-0" aria-label="个人空间说明">
                    <CircleHelp size={16} aria-hidden />
                  </Button>
                </TooltipTrigger>
                <TooltipContent side="bottom" className="whitespace-pre-line text-[13px] leading-5">
                  {'个人空间仅包含你本人，\n不可添加或移除成员'}
                </TooltipContent>
              </Tooltip>
            </TooltipProvider>
          )}
        </div>
        {topRight}
      </div>
      <div className="flex flex-1 flex-col gap-4 p-5">
        {/* 成员表 */}
        {/* 骨架对齐真实成员表(容器+表头+行,列宽一致,loading↔loaded 列位不跳) */}
        <Card className="overflow-hidden rounded-none border-0 shadow-none">
          {loading ? (
            <>
              <div className={`grid ${columns} items-center border-b border-divider bg-muted/50 px-4 py-3`}>
                <Skeleton.Block className="h-3 w-10" />
                <Skeleton.Block className="h-3 w-8" />
                <Skeleton.Block className="h-3 w-8" />
                {manageable && <Skeleton.Block className="h-3 w-8" />}
              </div>
              <ul className="m-0 list-none divide-y divide-divider p-0">
                {Array.from({ length: 4 }).map((_, i) => (
                  <li key={i} className={`grid ${columns} items-center px-4 py-4`}>
                    <div className="flex items-center gap-2">
                      <Skeleton.Block className="h-10 w-10 shrink-0 rounded-full" />
                      <Skeleton.Line className="w-24" />
                    </div>
                    <Skeleton.Block className="h-7 w-16 rounded" />
                    <div className="flex justify-end">
                      <Skeleton.Block className="h-7 w-7 rounded" />
                    </div>
                    {manageable && <Skeleton.Block className="h-3 w-8" />}
                  </li>
                ))}
              </ul>
            </>
          ) : members.length === 0 ? (
            <div className="px-4 py-5">
              <Empty
                title="暂无成员"
                description={manageable ? '可添加成员来协同管理空间' : '该空间暂无成员'}
                compact
              />
            </div>
          ) : (
            <>
              <div className={`grid ${columns} items-center border-b border-divider bg-muted/50 px-4 py-3`}>
                <TableHeaderText as="span">成员</TableHeaderText>
                <TableHeaderText as="span">身份</TableHeaderText>
                <TableHeaderText as="span">权限</TableHeaderText>
                {manageable && (
                  <TableHeaderText as="span" className="text-right">
                    操作
                  </TableHeaderText>
                )}
              </div>
              <ul className="m-0 list-none divide-y divide-divider p-0">
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
      </div>

      {/* 添加成员多选模态：条件挂载保证每次打开都是 fresh 状态（selectedUsers=[]） */}
      {addOpen && (
        <AddMembersModal
          open={addOpen}
          onOpenChange={setAddOpen}
          members={members}
          currentUid={currentUid}
          onAddMembers={onAddMembers}
          addMembersLoading={addMembersLoading}
          addMembersDisabledReason={addMembersDisabledReason}
        />
      )}

      {/* 申请加入 Modal（未加入团队时） */}
      <SpaceJoinForm
        space={space}
        open={joinOpen}
        onOpenChange={setJoinOpen}
        onSubmit={async (reason) => {
          if (!onRequestJoin) return false;
          return (await onRequestJoin(space, reason)) !== false;
        }}
      />
    </div>
  );
}

export default SpaceMemberList;
