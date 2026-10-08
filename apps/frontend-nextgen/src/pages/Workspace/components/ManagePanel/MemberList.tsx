import { MessageViewScopeBadge } from '@/components/MessageViewScope';
import {
  Badge,
  Button,
  Empty,
  IconButton,
  Skeleton,
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import type { GroupView, IdentityView, ParticipantView } from '@/domain/collaboration';
import { resolveAuthenticatedDisplayName } from '@/domain/userIdentity';
import { MoreHorizontal, Plus, UserX } from 'lucide-react';
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
  /** 区块标题（如「群成员管理」），提供时替代「N 个成员」计数文本，计数并入括号。 */
  headerLabel?: string;
  groupKind: GroupView['kind'];
  showMode?: boolean;
  /**
   * 徽标档位（验收微调）：
   * - full：群管理列表默认——类型 + 角色 + 状态 + 视角全量徽标。
   * - session：会话列表精简档——保留 Bot/用户 类型与 bot 发言状态（自动/禁言），角色仅显示
   *   管理角色（群主/主节点）；人类参与状态与视角标签不在列表重复展示（会话输入区上方已标识）。
   */
  badgePolicy?: 'full' | 'session';
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

/** 会话精简档是否显示角色徽标：仅管理角色（driver→群主 / manager→主节点）需要标识，成员/从节点不再标识。 */
function isManagementRole(participant: ParticipantView, groupKind: GroupView['kind']): boolean {
  if (groupKind === 'task_master_slave') return participant.role === 'manager';
  return participant.role === 'driver';
}

/** 状态标签色：自动/参与绿，禁言/旁观灰。 */
function getModeTone(participant: ParticipantView): 'success' | 'neutral' {
  if (participant.kind === 'bot') return participant.mode === 'auto' ? 'success' : 'neutral';
  return participant.mode === 'present' ? 'success' : 'neutral';
}

/** 窄面板标签折叠档（容器查询作用于成员行）：一级行宽 <400px 收起可选标签（普通角色/状态/视角）；
 *  二级 <300px 连管理角色（群主/主节点）也收起，只留类型标签，行内名字不再被挤压。 */
const COLLAPSE_OPTIONAL = '@max-w-[400px]:hidden';
const COLLAPSE_MANAGEMENT = '@max-w-[300px]:hidden';

function Avatar({ participant }: { participant: ParticipantView }) {
  const symbol = participant.name?.trim().charAt(0) || '?';
  return (
    <div
      className={
        participant.kind === 'bot'
          ? // bot 图标对齐 Bot 工坊信息列首字母圆标：主色软底（bg-primary/10 + text-primary）。
            'grid size-8 flex-none place-items-center rounded-full bg-primary/10 text-xs font-medium text-primary shadow-sm'
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
  headerLabel,
  groupKind,
  showMode = false,
  badgePolicy = 'full',
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
        <span className="text-sm font-medium text-foreground">
          {showLoading
            ? '加载中…'
            : headerLabel
            ? `${headerLabel}（${participants.length}）`
            : `${participants.length} 个成员`}
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
          <Empty compact title={emptyText} className="py-6" />
        )
      ) : (
        <div>
          {participants.map((participant) => {
            const displayName = resolveAuthenticatedDisplayName(
              { id: participant.actorId, kind: participant.kind, name: participant.name },
              authenticatedUserId ? { userId: authenticatedUserId, name: authenticatedUserName } : null,
            );
            // 标签可见性：会话精简档（badgePolicy=session）仅管理角色显示角色徽标，人类参与状态与视角不重复展示。
            const managementRole = isManagementRole(participant, groupKind);
            const modeLabel = getModeLabel(participant);
            const scope = participant.kind === 'human' ? participant.messageViewScope : undefined;
            const showRoleBadge = badgePolicy === 'full' || managementRole;
            const showModeBadge = showMode && !!modeLabel && (badgePolicy === 'full' || participant.kind === 'bot');
            const showScopeBadge = badgePolicy === 'full' && !!scope;
            // 存在可收起标签（角色/状态/视角）时渲染「...」展开入口；仅类型标签的行不渲染。
            const hasCollapsible = showRoleBadge || showModeBadge || showScopeBadge;
            return (
              <div
                key={participant.actorId}
                data-testid={`member-${participant.actorId}`}
                className="@container flex items-center gap-2 border-b border-border px-1 py-2 last:border-b-0 hover:bg-muted/50 transition-colors"
              >
                <Avatar participant={{ ...participant, name: displayName }} />
                {/* 名字可能被徽标挤压截断：hover 显示全称。 */}
                <TooltipProvider delayDuration={250}>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <span className="m-0 min-w-0 flex-1 truncate text-xs font-medium text-foreground">
                        {displayName}
                      </span>
                    </TooltipTrigger>
                    <TooltipContent>{displayName}</TooltipContent>
                  </Tooltip>
                </TooltipProvider>
                {/* 标签分类型着色（走 Badge 组件默认字号）：成员类型默认蓝；群主/主节点紫；自动/参与绿；禁言/旁观灰。
                    会话精简档（badgePolicy=session）：角色徽标仅管理角色显示；人类参与状态与视角标签不再重复展示。
                    窄面板折叠（验收微调）：行内容宽 <400px 收起可选标签、<300px 连管理角色也收起（容器查询类），
                    「...」入口 hover/聚焦展开被收起标签，成员名字不再被挤压。 */}
                <div className="flex shrink-0 flex-wrap items-center justify-end gap-1">
                  <Badge tone="primary">{participant.kind === 'bot' ? 'Bot' : '用户'}</Badge>
                  {showRoleBadge ? (
                    <Badge
                      tone={getRoleTone(participant, groupKind)}
                      className={managementRole ? COLLAPSE_MANAGEMENT : COLLAPSE_OPTIONAL}
                    >
                      {getRoleLabel(participant, groupKind)}
                    </Badge>
                  ) : null}
                  {showModeBadge && modeLabel ? (
                    <Badge tone={getModeTone(participant)} className={COLLAPSE_OPTIONAL}>
                      {modeLabel}
                    </Badge>
                  ) : null}
                  {showScopeBadge && scope ? (
                    <MessageViewScopeBadge scope={scope} className={COLLAPSE_OPTIONAL} />
                  ) : null}
                  {hasCollapsible ? (
                    <TooltipProvider delayDuration={250}>
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <Button
                            variant="ghost"
                            size="sm"
                            aria-label="更多成员标签"
                            className="hidden h-6 w-6 px-0 text-muted-foreground @max-w-[400px]:inline-flex"
                          >
                            <MoreHorizontal className="h-3.5 w-3.5" />
                          </Button>
                        </TooltipTrigger>
                        <TooltipContent className="flex max-w-[220px] flex-wrap gap-1 p-2">
                          {showRoleBadge ? (
                            <Badge tone={getRoleTone(participant, groupKind)}>
                              {getRoleLabel(participant, groupKind)}
                            </Badge>
                          ) : null}
                          {showModeBadge && modeLabel ? (
                            <Badge tone={getModeTone(participant)}>{modeLabel}</Badge>
                          ) : null}
                          {showScopeBadge && scope ? <MessageViewScopeBadge scope={scope} /> : null}
                        </TooltipContent>
                      </Tooltip>
                    </TooltipProvider>
                  ) : null}
                </div>
                {canManage && participant.role !== 'owner' && (
                  <ConfirmDialog
                    title={`移除成员 ${displayName}`}
                    description="移除后将无法参与当前协作。"
                    confirmText="确认移除"
                    confirmVariant="destructive"
                    onConfirm={() => void onRemove(participant.actorId)}
                  >
                    {/* 验收微调：移除入口为常驻图标按钮（IconButton 自带 Tooltip 提示）；
                        图标用 UserX（人形+叉号，比 UserMinus 减号更醒目、保留移除成员语义）；
                        中性灰常驻、悬停变红；可访问名称带成员名区分多行；点击仍走二次确认。 */}
                    <IconButton
                      label={`移除成员 ${displayName}`}
                      icon={<UserX className="h-4 w-4" aria-hidden />}
                      size="sm"
                      variant="ghost"
                      className="text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
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
