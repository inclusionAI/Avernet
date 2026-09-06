import { Button, IconButton } from '@/components/ui';
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
import { MoreHorizontal, Plus, Settings2, Share2, Trash2, Users } from 'lucide-react';
import React, { useState } from 'react';
import { AvatarTile } from '../AvatarTile';
import { ShareDialog } from '../ManagePanel/ShareDialog';
import { SessionScopeFilter } from '../SessionScopeFilter';
import type { GroupItemProps, SessionTab } from './GroupItem.types';
import { KIND_LABEL, MEMBERSHIP_LABEL } from './GroupItem.types';
import { GroupSessionsList } from './GroupSessionsList';

export type { SessionTab } from './GroupItem.types';

export const GroupItem = React.memo(function GroupItem({
  group,
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
          selected || expanded ? 'bg-primary/5' : 'bg-background hover:bg-accent/50',
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
          <AvatarTile
            label={group.name}
            className="rounded-full bg-secondary text-secondary-foreground ring-1 ring-border"
            fallbackContent={<Users className="h-4 w-4" aria-hidden="true" />}
          />
          <div className="min-w-0 flex-1">
            <div className="flex min-w-0 items-center gap-2">
              <TooltipProvider delayDuration={300}>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <span className="min-w-0 flex-1 truncate text-sm font-normal text-foreground">{group.name}</span>
                  </TooltipTrigger>
                  <TooltipContent>{group.name}</TooltipContent>
                </Tooltip>
              </TooltipProvider>
            </div>
            <TooltipProvider delayDuration={0}>
              <Tooltip>
                <TooltipTrigger asChild>
                  <div
                    aria-label={`协作群标签：${metadataLabel}`}
                    className="mt-1 flex min-w-0 items-center gap-1 truncate text-xs leading-4 text-muted-foreground"
                  >
                    {group.isPublic && (
                      <>
                        <span className="shrink-0 text-primary">公开</span>
                        <span aria-hidden="true" className="text-muted-foreground/50">
                          ·
                        </span>
                      </>
                    )}
                    <span className="shrink-0">{KIND_LABEL[group.kind]}</span>
                    <span aria-hidden="true" className="text-muted-foreground/50">
                      ·
                    </span>
                    <span className="shrink-0">{membershipLabel}</span>
                  </div>
                </TooltipTrigger>
                <TooltipContent>{metadataLabel}</TooltipContent>
              </Tooltip>
            </TooltipProvider>
          </div>
        </Button>
        <IconButton
          label="新建会话"
          size="sm"
          icon={<Plus className="h-4 w-4" />}
          className="rounded-md text-muted-foreground hover:bg-primary/10 hover:text-primary"
          onClick={(event) => {
            event.stopPropagation();
            onCreateSession(group.groupId);
          }}
        />
        <SessionScopeFilter
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
              icon={<MoreHorizontal className="h-4 w-4" />}
              className="rounded-md text-muted-foreground hover:bg-primary/10 hover:text-primary"
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
