// 空间成员单行：头像 + 名称 + 角色(可改/只读) + 移除。
// 角色列始终渲染下拉（对齐 PRD：borderless 纯文字下拉），disabled 态用提示文案(Tooltip)说明原因。
// 5 种禁用场景：个人空间 / 未加入团队 / 非管理员 / 创建者 / 最后一位管理员（§7.6）。
// 角色变更直接执行（PRD 交互），无降级二次确认；降级由「最后一位管理员不可改」前置 disabled 防护。
// 管理员色 warning 橙 / 成员色 muted-foreground 灰（对齐 PRD：color r==='admin'?'--warning':'--muted-foreground'）。
import { getCapabilities } from '@/capabilities';
import {
  Avatar,
  Button,
  ConfirmDialog,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui';
import type { Space, SpaceMember } from '@/domain/admin/models';
import { cn } from '@/utils/cn';
import { Trash2 } from 'lucide-react';

export type RoleCellValue = 'ADMIN' | 'MEMBER';

/** 角色列禁用态 + 提示文案（对齐 PRD tooltip 文案矩阵）。 */
export interface RoleCellState {
  disabled: boolean;
  /** 禁用时悬浮提示文案（对齐 PRD Tooltip）；启用时为空串，不显示 Tooltip。 */
  reason: string;
}

/**
 * 纯逻辑：根据空间归属/成员身份/当前用户管理权，判定角色列禁用态与提示文案。
 * 优先级：个人空间 → 未加入团队 → 非管理员 → 创建者 → 最后一位管理员 → 可改。
 */
export function getRoleState(
  space: Pick<Space, 'spaceType' | 'currentUserRole'>,
  member: Pick<SpaceMember, 'role' | 'isCreator'>,
  manageable: boolean,
  lastOwner: boolean,
): RoleCellState {
  if (space.spaceType === 'PERSONAL') return { disabled: true, reason: '个人空间不可修改角色' };
  const joined = space.currentUserRole === 'ADMIN' || space.currentUserRole === 'MEMBER';
  if (!joined) return { disabled: true, reason: '未加入的团队空间不可操作' };
  if (!manageable) return { disabled: true, reason: '仅管理员可修改角色' };
  if (member.isCreator) return { disabled: true, reason: '创建者不可变更角色' };
  if (member.role === 'ADMIN' && lastOwner) return { disabled: true, reason: '至少需保留一位管理员' };
  return { disabled: false, reason: '' };
}

export interface SpaceMemberRowProps {
  space: Space;
  member: SpaceMember;
  manageable: boolean;
  lastOwner: boolean;
  /** 角色变更：直接执行（PRD 交互，无二次确认）。 */
  onUpdateRole: (userId: string, newRole: RoleCellValue) => void | Promise<void>;
  onRemoveMember: (userId: string) => void | Promise<void>;
}

export function SpaceMemberRow({
  space,
  member,
  manageable,
  lastOwner,
  onUpdateRole,
  onRemoveMember,
}: SpaceMemberRowProps) {
  // 名称优先级：user_name(花名) → displayName → user_id。
  // mapper 已把 user_name ?? user_id 折叠进 userName，故 userName 命中即展示花名或工号；
  // displayName 作为花名缺失且 userName 异常为空时的兜底，工号始终是最后手段。
  const name = member.userName || member.displayName || member.userId;
  const { disabled: roleDisabled, reason } = getRoleState(space, member, manageable, lastOwner);
  const isAdmin = member.role === 'ADMIN';
  // PRD：角色色 admin=warning 橙 / member=muted-foreground 灰；用语义 text-* 类覆盖 trigger base 的 text-foreground。
  // 成员头像经 capability 解析：internal overlay 按 user_id(工号) 拼 antwork 照片 URL；
  // Open Core/null/加载失败时 <Avatar> 回退首字母占位（不硬编码内网 URL，保持 Open Core 纯净）。
  const avatarUrl = getCapabilities().getMemberAvatarUrl(member.userId).value ?? undefined;

  return (
    <li className="grid grid-cols-[minmax(0,1fr)_160px_80px] items-center px-4 py-2.5 transition-colors hover:bg-muted/40">
      {/* 成员 */}
      <div className="flex min-w-0 items-center gap-2">
        <Avatar name={name} src={avatarUrl} size={28} />
        <span className="truncate font-medium">{name}</span>
      </div>

      {/* 角色：始终渲染下拉（borderless），disabled 时 Tooltip 提示原因 */}
      <TooltipProvider>
        <Tooltip>
          <TooltipTrigger asChild>
            <span className="inline-flex">
              <Select
                value={member.role === 'UNKNOWN' ? 'MEMBER' : member.role}
                disabled={roleDisabled}
                onValueChange={(v) => {
                  if (v !== member.role) onUpdateRole(member.userId, v as RoleCellValue);
                }}
              >
                <SelectTrigger
                  className={cn(
                    'h-8 w-[90px] border-0 bg-transparent px-2 text-xs font-medium shadow-none',
                    roleDisabled ? 'cursor-not-allowed opacity-60' : 'hover:bg-accent/60',
                    isAdmin ? 'text-warning' : 'text-muted-foreground',
                  )}
                >
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="MEMBER">成员</SelectItem>
                  <SelectItem value="ADMIN">管理员</SelectItem>
                </SelectContent>
              </Select>
            </span>
          </TooltipTrigger>
          {reason ? <TooltipContent>{reason}</TooltipContent> : null}
        </Tooltip>
      </TooltipProvider>

      {/* 操作：删除（创建者不删） */}
      <div className="flex items-center justify-end">
        {manageable && !member.isCreator && (
          <ConfirmDialog
            title="确认移除该成员？"
            description={`${name} 将不再属于「${space.spaceName}」`}
            confirmText="移除"
            confirmVariant="destructive"
            onConfirm={() => onRemoveMember(member.userId)}
          >
            <span>
              <Button
                size="icon"
                variant="ghost"
                aria-label={`移除成员 ${name}`}
                className="h-7 w-7 text-destructive hover:bg-destructive/10"
              >
                <Trash2 size={14} />
              </Button>
            </span>
          </ConfirmDialog>
        )}
      </div>
    </li>
  );
}

export default SpaceMemberRow;
