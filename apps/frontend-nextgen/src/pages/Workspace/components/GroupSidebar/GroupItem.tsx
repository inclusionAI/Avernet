import { MessageViewScopeMenuButton } from '@/components/MessageViewScope';
import type { ParticipantView } from '@/domain/collaboration/types';
import { Avatar, Badge, Button, IconButton } from '@/components/ui';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/AlertDialog';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/Popover';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';
import { cn } from '@/utils/cn';
import { ChevronDown, MoreHorizontal, Plus, Settings2, Share2, Trash2 } from 'lucide-react';
import React, { useRef, useState } from 'react';
import { SharePopover } from '../ManagePanel/SharePopover';
import { SessionScopeFilter } from '../SessionScopeFilter';
import type { GroupItemProps, SessionTab } from './GroupItem.types';
import { KIND_LABEL, SIDEBAR_TAG_CLASS } from './GroupItem.types';
import { GroupSessionsList } from './GroupSessionsList';

export type { SessionTab } from './GroupItem.types';

/** 群类型标签配色：自由聊天=蓝、自定义协同=绿、任务协作=橙。 */
const KIND_TONE: Record<GroupItemProps['group']['kind'], 'primary' | 'success' | 'warning'> = {
  free_chat: 'primary',
  task_dag: 'success',
  task_master_slave: 'warning',
};

/** 头像堆最多展示成员数（dmore artboard-010「运营协作」行：2 个 16px 真实头像 + N+ 徽标）。 */
const AVATAR_STACK_MAX = 2;

function isHttpUrl(url: string): boolean {
  return /^https?:\/\//.test(url);
}

/**
 * 成员头像堆（dmore artboard-010「群目录一级_收起/展开」两级行首 34x16 容器实测）：
 * ≤2 个 16px 真实头像按 9px 步距叠放,与标题净距 4px（行 gap-2 由堆上 -mr-1 抵 4px）;
 * 超出部分以 N+ 徽标（8px 字、E8EAED 底）表达剩余人数。participants 未回填时不渲染行首
 * 标识（2026-10-12 用户反馈：群行不用文件夹图标,列表缺成员时由 hook 层按详情补拉）。
 */
export function GroupAvatarStack({ participants, totalCount }: { participants: ParticipantView[]; totalCount: number }) {
  const shown = participants.slice(0, AVATAR_STACK_MAX);
  const remaining = Math.max(0, totalCount - shown.length);
  if (shown.length === 0) return null;
  return (
    <span className="-mr-1 flex h-4 shrink-0 items-center" aria-label={`成员 ${totalCount} 人`}>
      {shown.map((participant, index) => (
        <span
          key={participant.actorId ?? participant.name}
          className={cn('rounded-full ring-1 ring-background', index > 0 && '-ml-[7px]')}
        >
          <Avatar
            name={participant.name}
            src={participant.avatarUrl && isHttpUrl(participant.avatarUrl) ? participant.avatarUrl : undefined}
            size={16}
          />
        </span>
      ))}
      {remaining > 0 && (
        <span
          aria-hidden="true"
          className="-ml-[7px] flex h-4 min-w-4 items-center justify-center rounded-full bg-secondary px-0.5 text-[8px] leading-none text-secondary-foreground ring-1 ring-background"
        >
          {remaining}+
        </span>
      )}
    </span>
  );
}

export const GroupItem = React.memo(function GroupItem({
  group,
  viewerKind,
  expanded,
  sessions,
  sessionTab,
  onSessionTabChange,
  favoriteSessionIds,
  selectedGroupId,
  selectedSessionId,
  onSelectGroup,
  onToggleGroupExpanded,
  onSelectSession,
  onToggleFavorite,
  onCreateSession,
  onManageGroup,
  onManageSession,
  onRenameSession,
  onDeleteSession,
  onShareSession,
  activeIdentity,
  onShareGroup,
  onDissolveGroup,
  totalSessionCount,
  hasMoreSessions,
  isLoadingMoreSessions,
  onLoadMoreSessions,
  error,
  loadMoreError,
  onRetrySessions,
}: GroupItemProps) {
  const [actionsOpen, setActionsOpen] = useState(false);
  const [dissolveOpen, setDissolveOpen] = useState(false);
  const [shareOpen, setShareOpen] = useState(false);
  const shareAnchorRef = useRef<HTMLSpanElement | null>(null);
  const safeSessions = sessions ?? [];
  // 群接口只返回当前页会话的收藏状态；仍有下一页时，收藏总数尚不能确定。
  const favoriteCount = hasMoreSessions
    ? undefined
    : safeSessions.filter((s) => favoriteSessionIds.includes(s.sessionId)).length;
  // 操作区常显条件：展开 / 收藏 Tab / dots 菜单开启（2026-10-12 用户反馈移除选中态——
  // 群行不再有选中背景）。常显必须与下方标题让位 pr-[88px] 同条件生效，否则按钮与群名称重叠。
  const actionsPinned = expanded || sessionTab === 'favorite' || actionsOpen;
  // 头像堆成员人数口径：participantCount 为列表接口权威总数；缺计数时用明细数组兜底。
  // participants 明细由 useGroupWorkspace.enrichParticipants 按群详情补拉（列表接口只有计数）。
  const stackTotalCount = group.participantCount > 0 ? group.participantCount : group.participants.length;

  const handleCardClick = () => {
    if (selectedGroupId !== group.groupId) onSelectGroup(group.groupId);
    onToggleGroupExpanded(group.groupId);
  };
  const handleSessionScopeChange = (tab: SessionTab) => {
    onSessionTabChange(tab);
    if (!expanded) onToggleGroupExpanded(group.groupId);
  };
  // 打开即止：生成流程收敛在 SharePopover 气泡内（选有效期 → 生成链接），本入口不再立即请求；
  // 气泡锚定在 dots 触发钮下（菜单关闭后气泡从 dots 位置展开）。
  const handleShare = () => {
    setActionsOpen(false);
    setShareOpen(true);
  };
  const openDissolveConfirm = () => {
    setActionsOpen(false);
    setDissolveOpen(true);
  };

  return (
    <div className="py-0.5">
      {/* v2 群一级行（dmore artboard-010「群目录一级_*」32px）：行背景胶囊与 hover 热区
          分离（同对话 ConversationSessionRow 几何）；行首恒为成员头像堆（收起/展开同款，
          详见 GroupAvatarStack）。无选中态（用户反馈）：胶囊恒为 hover 弱底，
          选中群只经展开 chevron 与右侧会话列表表达。 */}
      <div className="group/row relative flex min-h-8 items-center">
        <span
          aria-hidden="true"
          className="absolute inset-y-0 left-2 right-2 rounded-md bg-muted/60 opacity-0 transition-opacity group-hover/row:opacity-100 group-focus-within/row:opacity-100 [@media(hover:none)]:opacity-100"
        />
        <Button
          variant="ghost"
          aria-label={group.name}
          aria-expanded={expanded}
          onClick={handleCardClick}
          className={cn(
            'relative flex h-8 min-w-0 flex-1 items-center justify-start gap-2 rounded-md pl-2 pr-2 text-left hover:bg-transparent',
            // 长标题让位：hover/聚焦/触屏/菜单开启时右界让出操作区（3 个 24px 图标 + right-2）。
            'group-hover/row:pr-[88px] group-focus-within/row:pr-[88px] [@media(hover:none)]:pr-[88px] group-has-[[data-state=open]]/row:pr-[88px]',
            // 操作区常显（展开/收藏 Tab/dots 开启）时同样让位，避免常显按钮与群名称重叠。
            actionsPinned && 'pr-[88px]',
          )}
        >
          <GroupAvatarStack participants={group.participants} totalCount={stackTotalCount} />
          <TooltipProvider delayDuration={300}>
            <Tooltip>
              <TooltipTrigger asChild>
                <span className="min-w-0 flex-1 truncate text-[13px] leading-[18px] tracking-[-0.08px] text-content-strong">
                  {group.name}
                </span>
              </TooltipTrigger>
              <TooltipContent>{group.name}</TooltipContent>
            </Tooltip>
          </TooltipProvider>
          <Badge tone={KIND_TONE[group.kind]} className={cn('shrink-0 whitespace-nowrap', SIDEBAR_TAG_CLASS)}>
            {KIND_LABEL[group.kind]}
          </Badge>
          {/* 设计稿：仅展开态行内展示 chevron-down（收起态无箭头，点击整行仍可展开）。 */}
          {expanded ? <ChevronDown className="h-3 w-3 shrink-0 text-muted-foreground" aria-hidden="true" /> : null}
        </Button>
        {/* v2 hover 操作区（设计稿未画，能力保留落位于行右缘轻量图标组）：
            新建会话入口 + 会话范围（全部/收藏）+ dots 菜单（管理/分享/解散）。
            显隐：hover/聚焦/触屏常显；展开或收藏 Tab 或 dots 开启期间常显防闪跳（选中不再常显，
            同 2026-10-12 用户反馈移除的选中态）。 */}
        <div
          className={cn(
            'absolute right-2 top-1/2 z-10 flex -translate-y-1/2 items-center gap-0.5',
            'opacity-0 transition-opacity group-hover/row:opacity-100 group-focus-within/row:opacity-100 [@media(hover:none)]:opacity-100',
            actionsPinned && 'opacity-100',
          )}
          onClick={(event) => event.stopPropagation()}
        >
          {viewerKind === 'user' ? (
            <MessageViewScopeMenuButton onSelect={(scope) => onCreateSession(group.groupId, scope)} />
          ) : (
            <IconButton
              label="新建会话"
              size="sm"
              icon={<Plus className="h-3.5 w-3.5" />}
              className="h-6 w-6 rounded-md text-muted-foreground hover:bg-muted hover:text-foreground"
              onClick={(event) => {
                event.stopPropagation();
                onCreateSession(group.groupId);
              }}
            />
          )}
          <SessionScopeFilter
            compact
            value={sessionTab}
            onChange={handleSessionScopeChange}
            allCount={totalSessionCount}
            favoriteCount={favoriteCount}
          />
          <Popover open={actionsOpen} onOpenChange={setActionsOpen}>
            <PopoverTrigger asChild>
              {/* span 承载气泡锚点 ref（IconButton 本体让给菜单 trigger 的 ref 合并）。 */}
              <span ref={shareAnchorRef} className="flex shrink-0">
                <IconButton
                  label="协作群操作"
                  size="sm"
                  icon={<MoreHorizontal className="h-3.5 w-3.5" />}
                  className="h-6 w-6 rounded-md text-muted-foreground hover:bg-muted hover:text-foreground"
                />
              </span>
            </PopoverTrigger>
            <PopoverContent align="end" className="w-44 p-1">
              <Button
                variant="ghost"
                className="h-auto w-full justify-start gap-2 px-2 py-2 text-xs"
                onClick={() => {
                  setActionsOpen(false);
                  onManageGroup(group.groupId);
                }}
              >
                <Settings2 className="h-3.5 w-3.5" aria-hidden="true" />
                管理协作群
              </Button>
              <Button
                variant="ghost"
                className="h-auto w-full justify-start gap-2 px-2 py-2 text-xs"
                onClick={() => void handleShare()}
              >
                <Share2 className="h-3.5 w-3.5" aria-hidden="true" />
                分享协作群
              </Button>
              <Button
                variant="ghost"
                className="h-auto w-full justify-start gap-2 px-2 py-2 text-xs text-destructive"
                onClick={openDissolveConfirm}
              >
                <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
                解散协作群
              </Button>
            </PopoverContent>
          </Popover>
        </div>
      </div>

      {expanded && (
        <GroupSessionsList
          group={group}
          sessions={sessions}
          sessionTab={sessionTab}
          favoriteSessionIds={favoriteSessionIds}
          selectedSessionId={selectedSessionId}
          hasMoreSessions={hasMoreSessions}
          isLoadingMoreSessions={isLoadingMoreSessions}
          onLoadMoreSessions={onLoadMoreSessions}
          error={error}
          loadMoreError={loadMoreError}
          onRetrySessions={onRetrySessions}
          onSelectSession={onSelectSession}
          onToggleFavorite={onToggleFavorite}
          onManageSession={onManageSession}
          onRenameSession={onRenameSession}
          onDeleteSession={onDeleteSession}
          onShareSession={onShareSession}
          activeIdentity={activeIdentity}
        />
      )}

      <SharePopover
        open={shareOpen}
        anchorRef={shareAnchorRef}
        title="协作群"
        onCreate={(expiresInSeconds) => onShareGroup(group.groupId, expiresInSeconds)}
        onClose={() => setShareOpen(false)}
      />

      <AlertDialog open={dissolveOpen} onOpenChange={setDissolveOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>解散协作群</AlertDialogTitle>
            <AlertDialogDescription>解散后将无法恢复“{group.name}”及其会话，确定继续吗？</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction variant="destructive" onClick={() => onDissolveGroup(group.groupId)}>
              确认解散
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
});