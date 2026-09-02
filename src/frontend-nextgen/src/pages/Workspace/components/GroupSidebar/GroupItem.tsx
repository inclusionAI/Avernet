import { Button, IconButton, Skeleton } from '@/components/ui';
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
import type { GroupView, SessionView } from '@/domain/collaboration/types';
import type { DomainResult } from '@/services/workspace/identityService';
import { cn } from '@/utils/cn';
import { MoreHorizontal, Plus, Settings2, Share2, Trash2, Users } from 'lucide-react';
import React, { useState } from 'react';
import { AvatarTile } from '../AvatarTile';
import { ShareDialog } from '../ManagePanel/ShareDialog';
import { SessionToolbar } from '../SessionToolbar';
import { SessionItem } from './SessionItem';

export type SessionTab = 'all' | 'favorite';

interface GroupItemProps {
  group: GroupView;
  expanded: boolean;
  sessions: SessionView[] | undefined;
  sessionTab: SessionTab;
  onSessionTabChange: (t: SessionTab) => void;
  favoriteSessionIds: string[];
  selectedGroupId: string | null;
  selectedSessionId: string | null;
  onSelectGroup: (groupId: string) => void;
  onToggleGroupExpanded: (groupId: string) => void;
  onSelectSession: (groupId: string, sessionId: string) => void;
  onToggleFavorite: (sessionId: string) => void;
  onCreateSession: (groupId: string) => void;
  onManageGroup: (groupId: string) => void;
  onManageSession: (groupId: string, sessionId: string) => void;
  onShareGroup: (groupId: string) => Promise<DomainResult<{ invitationUrl: string }>>;
  onDissolveGroup: (groupId: string) => void;
  totalSessionCount?: number;
  hasMoreSessions: boolean;
  isLoadingMoreSessions: boolean;
  onLoadMoreSessions: () => Promise<void>;
}

const KIND_LABEL: Record<GroupView['kind'], string> = {
  free_chat: '自由聊天',
  task_master_slave: '任务协作',
  task_dag: '自定义协同',
};

const MEMBERSHIP_LABEL: Record<NonNullable<GroupView['membership']>, string> = {
  direct: '固定群成员',
  session_only: '仅参与临时会话',
};

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
}: GroupItemProps) {
  const [actionsOpen, setActionsOpen] = useState(false);
  const [dissolveOpen, setDissolveOpen] = useState(false);
  const [shareOpen, setShareOpen] = useState(false);
  const [shareUrl, setShareUrl] = useState<string | null>(null);
  const [sharing, setSharing] = useState(false);
  const loaded = sessions !== undefined;
  const safeSessions = sessions ?? [];
  const visibleSessions =
    sessionTab === 'favorite' ? safeSessions.filter((s) => favoriteSessionIds.includes(s.sessionId)) : safeSessions;
  // 群接口只返回当前页会话的收藏状态；仍有下一页时，收藏总数尚不能确定。
  const favoriteCount = hasMoreSessions
    ? undefined
    : safeSessions.filter((s) => favoriteSessionIds.includes(s.sessionId)).length;
  const membershipLabel = MEMBERSHIP_LABEL[group.membership ?? 'direct'];
  const selected = selectedGroupId === group.groupId;

  const handleCardClick = () => {
    if (selectedGroupId !== group.groupId) onSelectGroup(group.groupId);
    onToggleGroupExpanded(group.groupId);
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
          'group relative flex min-h-[72px] items-center gap-3 border-b border-transparent px-[18px] py-3 transition-colors',
          selected ? 'bg-primary/5' : 'bg-transparent hover:bg-accent/50',
        )}
      >
        {selected && (
          <span aria-hidden="true" className="absolute bottom-2 left-0 top-2 w-[3px] rounded-r-sm bg-primary" />
        )}
        <Button
          variant="ghost"
          aria-label={group.name}
          aria-expanded={expanded}
          onClick={handleCardClick}
          className="flex h-auto min-w-0 flex-1 items-center justify-start gap-2.5 rounded-none px-0 py-1 text-left hover:bg-transparent"
        >
          <AvatarTile label={group.name} fallbackContent={<Users className="h-4 w-4" aria-hidden="true" />} />
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
            <div className="mt-1 flex min-w-0 items-center gap-1.5 truncate text-xs leading-4 text-muted-foreground">
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
          </div>
        </Button>
        <IconButton
          label="新建会话"
          size="sm"
          icon={<Plus className="h-4 w-4" />}
          className="rounded-md"
          onClick={(event) => {
            event.stopPropagation();
            onCreateSession(group.groupId);
          }}
        />
        <Popover open={actionsOpen} onOpenChange={setActionsOpen}>
          <PopoverTrigger asChild>
            <IconButton
              label="协作群操作"
              size="sm"
              icon={<MoreHorizontal className="h-4 w-4" />}
              className="rounded-md"
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
        <div aria-label={`协作群会话列表：${group.name}`} className="border-b border-border/60 bg-background">
          <SessionToolbar
            value={sessionTab}
            onChange={onSessionTabChange}
            allCount={totalSessionCount}
            favoriteCount={favoriteCount}
          />
          <div className="overflow-hidden border-b border-border/60 bg-background">
            {!loaded ? (
              <div>
                {[1, 2, 3].map((i) => (
                  <Skeleton.Block key={i} className="h-16 w-full rounded-none border-b border-border last:border-b-0" />
                ))}
              </div>
            ) : visibleSessions.length === 0 ? (
              <div className="px-3 py-5">
                <span className="text-xs text-muted-foreground">
                  {sessionTab === 'favorite' ? '暂无已收藏会话' : '暂无协作群临时会话'}
                </span>
              </div>
            ) : (
              visibleSessions.map((session) => (
                <SessionItem
                  key={session.sessionId}
                  session={session}
                  favorite={favoriteSessionIds.includes(session.sessionId)}
                  selected={selectedSessionId === session.sessionId}
                  onSelectSession={(sessionId) => onSelectSession(group.groupId, sessionId)}
                  onToggleFavorite={onToggleFavorite}
                  onManageSession={(sessionId) => onManageSession(group.groupId, sessionId)}
                />
              ))
            )}
          </div>
          {hasMoreSessions && (
            <div className="flex justify-center border-b border-border/60 px-[18px] pb-1 pt-3">
              <Button
                variant="ghost"
                size="sm"
                disabled={isLoadingMoreSessions}
                onClick={(event) => {
                  event.stopPropagation();
                  void onLoadMoreSessions();
                }}
                className="h-8 rounded-none px-3 text-xs text-muted-foreground hover:bg-accent/60 hover:text-foreground"
              >
                {isLoadingMoreSessions ? '正在加载…' : '加载更多'}
              </Button>
            </div>
          )}
        </div>
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
