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
import { ChevronDown, ChevronRight, MoreHorizontal, Plus, Settings2, Share2, Trash2, Users } from 'lucide-react';
import React, { useState } from 'react';
import { AvatarTile } from '../AvatarTile';
import { ShareDialog } from '../ManagePanel/ShareDialog';
import { SessionScopeFilter } from '../SessionScopeFilter';
import type { GroupItemProps, SessionTab } from './GroupItem.types';
import { KIND_LABEL, MEMBERSHIP_LABEL, SIDEBAR_TAG_CLASS } from './GroupItem.types';
import { GroupSessionsList } from './GroupSessionsList';

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
  const [shareUrl, setShareUrl] = useState<string | null>(null);
  const [sharing, setSharing] = useState(false);
  const safeSessions = sessions ?? [];
  // 群接口只返回当前页会话的收藏状态；仍有下一页时，收藏总数尚不能确定。
  const favoriteCount = hasMoreSessions
    ? undefined
    : safeSessions.filter((s) => favoriteSessionIds.includes(s.sessionId)).length;
  const membershipLabel = MEMBERSHIP_LABEL[group.membership ?? 'direct'];
  const metadataLabel = [group.isPublic ? '公开' : null, KIND_LABEL[group.kind], membershipLabel]
    .filter(Boolean)
    .join(' · ');
  const selected = selectedGroupId === group.groupId;

  const handleCardClick = () => {
    if (selectedGroupId !== group.groupId) onSelectGroup(group.groupId);
    onToggleGroupExpanded(group.groupId);
  };
  const handleSessionScopeChange = (tab: SessionTab) => {
    onSessionTabChange(tab);
    if (!expanded) onToggleGroupExpanded(group.groupId);
  };
  const handleShare = async () => {
    setActionsOpen(false);
    setShareOpen(true);
    setShareUrl(null);
    setSharing(true);
    const result = await onShareGroup(group.groupId);
    setSharing(false);
    if (result.ok) setShareUrl(result.data.invitationUrl);
  };
  const openDissolveConfirm = () => {
    setActionsOpen(false);
    setDissolveOpen(true);
  };

  return (
    <div>
      <div
        className={cn(
          'group relative flex min-h-16 items-center gap-3 px-4 py-2.5 transition-colors',
          selected || expanded ? 'bg-muted' : 'hover:bg-accent/50',
        )}
      >
        {selected && (
          <span aria-hidden="true" className="absolute bottom-2 left-0 top-2 w-[3px] rounded-r-sm bg-primary" />
        )}
        <Button
          variant="ghost"
          aria-label={group.name}
          aria-expanded={expanded}
          aria-current={selected ? 'page' : undefined}
          onClick={handleCardClick}
          className="flex h-auto min-w-0 flex-1 items-center justify-start gap-3 rounded-none px-0 py-1 text-left hover:bg-transparent"
        >
          {/* v1.4：选中/展开态头像品牌浅底弱化强调（浅蓝底+蓝图标，实心反色试装后按用户反馈调轻）。 */}
          <AvatarTile
            label={group.name}
            className={cn(
              'rounded-full ring-1',
              selected || expanded
                ? 'bg-primary/15 text-primary ring-primary/30'
                : 'bg-secondary text-secondary-foreground ring-border',
            )}
            fallbackContent={<Users className="h-4 w-4" aria-hidden="true" />}
          />
          <div className="min-w-0 flex-1">
            <div className="flex min-w-0 items-center gap-2">
              <TooltipProvider delayDuration={300}>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <span className="min-w-0 flex-1 truncate text-sm font-medium text-foreground">{group.name}</span>
                  </TooltipTrigger>
                  <TooltipContent>{group.name}</TooltipContent>
                </Tooltip>
              </TooltipProvider>
            </div>
            <div
              aria-label={`协作群标签：${metadataLabel}`}
              className="mt-1 flex min-w-0 items-center gap-1 truncate text-xs leading-4 group-hover:pr-20"
            >
              {group.isPublic && (
                <Badge tone="success" className={SIDEBAR_TAG_CLASS}>
                  公开
                </Badge>
              )}
              <Badge tone={KIND_TONE[group.kind]} className={SIDEBAR_TAG_CLASS}>
                {KIND_LABEL[group.kind]}
              </Badge>
              <Badge tone="primary" className={SIDEBAR_TAG_CLASS}>
                {membershipLabel}
              </Badge>
            </div>
          </div>
        </Button>
        {/* v1.5：操作区绝对定位悬浮不占位（用户验收决策）——badge/群名用满行宽，
            解决 v1.4 透明占位把不可收缩的 badge 组挤出截断（「仅参与临…」）。
            满行覆盖（用户预览四轮决策）：inset-y-0 撑满行高、右缘止于箭头左缘
            （行右 30px = 16px padding + 14px 箭头；箭头是前景不罩半透明层），
            底色走 global.css 工具类（渐变叠底色真实合成，与行视觉零色差、不透字；
            选中/展开 = muted 实色；可见性由 opacity 控制，无需 group-hover 变体修饰
            手写类），左缘 20px mask 渐隐；显隐语义保留。按钮组在满高容器内垂直居中。 */}
        <div
          className={cn(
            'absolute inset-y-0 right-[30px] z-10 flex items-center gap-0.5 pl-5',
            'mask-[linear-gradient(to_right,transparent,black_20px)]',
            selected || expanded ? 'sidebar-actions-cover-selected' : 'sidebar-actions-cover-hover',
            'opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100 [@media(hover:none)]:opacity-100',
            (selected || expanded || sessionTab === 'favorite' || actionsOpen) && 'opacity-100',
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
              className="h-6 w-6 rounded-md text-muted-foreground hover:bg-primary/10 hover:text-primary"
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
              <IconButton
                label="协作群操作"
                size="sm"
                icon={<MoreHorizontal className="h-3.5 w-3.5" />}
                className="h-6 w-6 rounded-md text-muted-foreground hover:bg-primary/10 hover:text-primary"
                onClick={(event) => event.stopPropagation()}
              />
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
        {/* 折叠/展开箭头固定在行最右（操作区之后），与 Bot 行同构，不随操作区显隐跳动；
            展开实心箭头主色、收起空心箭头弱化色。 */}
        {expanded ? (
          <ChevronDown className="h-3.5 w-3.5 shrink-0 text-primary" aria-hidden="true" />
        ) : (
          <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
        )}
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

      <ShareDialog
        open={shareOpen}
        title="协作群"
        inviting={sharing}
        invitationUrl={shareUrl}
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
