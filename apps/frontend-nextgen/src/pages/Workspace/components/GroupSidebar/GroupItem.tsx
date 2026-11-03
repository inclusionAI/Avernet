import { MessageViewScopeMenuButton } from '@/components/MessageViewScope';
import { Badge, Button, IconButton } from '@/components/ui';
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
import { GroupAvatarStack } from './GroupAvatarStack';
import type { GroupItemProps, SessionTab } from './GroupItem.types';
import { KIND_LABEL } from './GroupItem.types';
import { GroupSessionsList } from './GroupSessionsList';

// 头像堆已下沉至同目录 GroupAvatarStack（GroupHeader 同源引用），保留再导出兼容既有导入面。
export { GroupAvatarStack } from './GroupAvatarStack';
export type { SessionTab } from './GroupItem.types';

/** 群类型标签配色：自由聊天=蓝、自定义协同=绿、任务协作=橙。 */
const KIND_TONE: Record<GroupItemProps['group']['kind'], 'primary' | 'success' | 'warning'> = {
  free_chat: 'primary',
  task_dag: 'success',
  task_master_slave: 'warning',
};

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
      {/* 一级行统一为 52px：固定头像槽位，头像和操作相对两行文字整体居中。 */}
      <div className="group/row relative flex min-h-13 items-center">
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
            'relative flex h-auto min-h-13 min-w-0 flex-1 items-center justify-start gap-2 rounded-md py-1 pl-4 pr-2 text-left hover:bg-transparent',
            // 长标题让位：hover/聚焦/触屏/菜单开启时右界让出操作区（3 个 24px 图标 + right-2）。
            'group-hover/row:pr-[88px] group-focus-within/row:pr-[88px] [@media(hover:none)]:pr-[88px] group-has-[[data-state=open]]/row:pr-[88px]',
            // 操作区常显（展开/收藏 Tab/dots 开启）时同样让位，避免常显按钮与群名称重叠。
            actionsPinned && 'pr-[88px]',
          )}
        >
          <span className="flex h-9 w-9 shrink-0 items-center justify-center [&>span]:mr-0">
            <GroupAvatarStack participants={group.participants} totalCount={stackTotalCount} size={20} />
          </span>
          <span className="flex min-w-0 flex-1 flex-col items-start gap-0.5">
            <TooltipProvider delayDuration={300}>
              <Tooltip>
                <TooltipTrigger asChild>
                  <span className="w-full min-w-0 truncate text-[13px] font-normal leading-5 text-content-strong">
                    {group.name}
                  </span>
                </TooltipTrigger>
                <TooltipContent>{group.name}</TooltipContent>
              </Tooltip>
            </TooltipProvider>
            <Badge
              tone={KIND_TONE[group.kind]}
              className="block max-w-full truncate rounded px-1.5 py-0.5 text-[10px] font-normal leading-3"
            >
              {KIND_LABEL[group.kind]}
            </Badge>
          </span>
          {/* 箭头常驻 + 旋转过渡（2026-10-22 对话侧同语言）：收起 -rotate-90 提示「可展开」，
              展开 归零；替代原「仅展开态显示」的收起态零暗示（点击整行仍可展开，语义不变）。 */}
          <ChevronDown
            className={cn(
              'h-3.5 w-3.5 shrink-0 text-muted-foreground transition-transform duration-150',
              !expanded && '-rotate-90',
            )}
            aria-hidden="true"
          />
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
