import { IconButton } from '@/components/ui';
import type { GroupView, SessionView } from '@/domain/collaboration';
import type { GroupChatFavoritesModel } from '@/pages/Workspace/hooks/useGroupChatHeaderPanels';
import type { DomainResult } from '@/services/workspace/identityService';
import { cn } from '@/utils/cn';
import { Bot, ChevronRight, Copy, FolderOpen, History, Monitor, Settings2, Share2, SlidersHorizontal, Star, Users } from 'lucide-react';
import { useRef, useState } from 'react';
import { toast } from 'sonner';
import { GroupAvatarStack } from './GroupSidebar/GroupItem';
import { SharePopover } from './ManagePanel/SharePopover';

export type GroupPanelKind = 'none' | 'members' | 'manage' | 'sessionManage' | 'resources' | 'history';

export interface GroupHeaderProps {
  selectedGroup: GroupView | null;
  selectedSession: SessionView | null;
  onOpenSessionList?: () => void;
  canManageGroup?: unknown;
  activePanel: GroupPanelKind;
  onTogglePanel: (panel: GroupPanelKind) => void;
  /** 收藏模型（useGroupChatHeaderPanels 提供；无会话时 disabled 渲染，chat 同构语义）。 */
  favorites: GroupChatFavoritesModel;
  /** 副屏镜像开合态（PanelHandle 无 isOpen 查询，上层 hook 本地镜像）。 */
  sidePaneOpen: boolean;
  /** 会话无 <AixUI type="panel"> 副屏内容时禁用（openPanel 会被 autoCloseWhenEmpty 弹回）。 */
  sidePaneDisabled?: boolean;
  onToggleSidePane: () => void;
  onRequestDissolve?: () => void;
  onRequestShareGroup: (expiresInSeconds?: number) => Promise<DomainResult<{ invitationUrl: string }>>;
  onRequestShareSession: (expiresInSeconds?: number) => Promise<DomainResult<{ invitationUrl: string }>>;
}

/**
 * header chip（dmore artboard-003 群对话标题栏，48px 顶栏）：群身份 chip（头像堆/群名/会话 Key
 * 胶囊/用户与 Bot 计数）+ 右侧 28px 图标钮列。六槽位按设计稿 x=0/32/64/96/128/160 全量落地：
 * 收藏会话 / 分享 / 会话管理 / 历史消息 / 资源管理 / 副屏——其中五枚与 chat 页 ChatHeaderActions
 * 同构（Star/SlidersHorizontal/History/FolderOpen/Monitor），分享为协作群特有（SharePopover 气泡）。
 * 历史消息由右缘 GroupHistorySidebar 承载；连接状态文字不在 header 呈现：集中在 composer 帽条
 * ComposerStatusBar（change design 决策 7/8）。
 */
function HeaderIcon({
  label,
  icon,
  active,
  onClick,
  disabled,
  className,
}: {
  label: string;
  icon: React.ReactNode;
  active: boolean;
  onClick: () => void;
  disabled?: boolean;
  className?: string;
}) {
  return (
    <IconButton
      label={label}
      icon={icon}
      size="sm"
      disabled={disabled}
      aria-pressed={active}
      onClick={onClick}
      className={cn('rounded-md text-muted-foreground', active && 'bg-selected text-foreground', className)}
    />
  );
}

export function GroupHeader({
  selectedGroup,
  selectedSession,
  onOpenSessionList,
  activePanel,
  onTogglePanel,
  favorites,
  sidePaneOpen,
  sidePaneDisabled,
  onToggleSidePane,
  onRequestShareGroup,
  onRequestShareSession,
}: GroupHeaderProps) {
  // 打开即止：生成流程收敛在 SharePopover 气泡（有效期 → 生成链接），入口只选目标；
  // 气泡经 ref 锚定在分享图标正下方（2026-10-13 走查反馈：不用弹窗用气泡）。
  const [shareOpen, setShareOpen] = useState(false);
  const [shareTarget, setShareTarget] = useState<'session' | 'group'>('session');
  const shareAnchorRef = useRef<HTMLSpanElement | null>(null);

  const participants = selectedGroup?.participants ?? [];
  const humans = participants.filter((participant) => participant.kind === 'human').length;
  const bots = participants.filter((participant) => participant.kind === 'bot').length;
  // 计数兜底：participants 未回填时退回列表接口的 participantCount 单计数。
  const totalCount = selectedGroup?.participantCount || participants.length;

  const handleShareSession = () => {
    setShareTarget('session');
    setShareOpen(true);
  };
  const handleShareGroup = () => {
    setShareTarget('group');
    setShareOpen(true);
  };

  const copySessionKey = async () => {
    if (!selectedSession?.sessionId) return;
    try {
      await navigator.clipboard.writeText(selectedSession.sessionId);
      toast.success('会话 Key 已复制');
    } catch {
      toast.error('复制失败，请手动复制。');
    }
  };

  return (
    <>
      <header className="flex h-12 items-center gap-2 border-b border-border bg-card px-3 sm:px-4">
        {onOpenSessionList ? (
          <IconButton
            label="打开会话列表"
            icon={<ChevronRight className="h-5 w-5" aria-hidden />}
            size="sm"
            className="shrink-0 lg:hidden"
            onClick={onOpenSessionList}
          />
        ) : null}

        {selectedGroup ? (
          /* 群身份 chip：rounded-10 描边胶囊，白底悬空于顶栏（artboard-010 358×32 实测）。 */
          <div className="flex min-w-0 max-w-full flex-1 items-center gap-2 sm:flex-none">
            <div
              role="group"
              className="flex h-8 min-w-0 items-center gap-2 rounded-[10px] border border-border bg-card px-2.5"
              aria-label={`协作群 ${selectedGroup.name}`}
            >
              <GroupAvatarStack participants={participants} totalCount={totalCount} />
              <span className="truncate text-[13px] leading-[18px] text-zinc-600">{selectedGroup.name}</span>
              {selectedSession ? (
                <span className="flex h-5 max-w-[180px] shrink-0 items-center gap-1 rounded-full bg-secondary px-2">
                  <span className="truncate text-[11px] leading-[16.5px] text-foreground/85" title={selectedSession.sessionId}>
                    {selectedSession.sessionId}
                  </span>
                  <button
                    type="button"
                    aria-label="复制会话 Key"
                    onClick={() => void copySessionKey()}
                    className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-foreground/10 hover:text-foreground"
                  >
                    <Copy className="h-3 w-3" aria-hidden />
                  </button>
                </span>
              ) : null}
              {participants.length > 0 || totalCount > 0 ? (
                <>
                  <span aria-hidden className="h-3 w-px shrink-0 bg-foreground/10" />
                  <span className="flex shrink-0 items-center gap-1" aria-label={`${humans || totalCount} 用户`}>
                    <Users className="h-3 w-3 text-zinc-400" aria-hidden />
                    <span className="text-xs leading-4 text-zinc-400">{humans || totalCount}</span>
                  </span>
                  {bots > 0 ? (
                    <span className="flex shrink-0 items-center gap-1" aria-label={`${bots} Bot`}>
                      <Bot className="h-3 w-3 text-zinc-400" aria-hidden />
                      <span className="text-xs leading-4 text-zinc-400">{bots}</span>
                    </span>
                  ) : null}
                </>
              ) : null}
            </div>
          </div>
        ) : null}

        <div className="flex flex-1 items-center justify-end gap-1">
          {selectedSession || selectedGroup ? (
            /* 收藏会话：chat 同构禁用态（无会话渲染 disabled 而非隐藏）；收藏态星标填充。 */
            <HeaderIcon
              label={favorites.favorite ? '取消收藏当前会话' : '收藏当前会话'}
              icon={<Star className={cn('size-4', favorites.favorite && 'fill-star text-star')} aria-hidden />}
              active={favorites.favorite}
              disabled={favorites.disabled}
              onClick={favorites.onToggle}
            />
          ) : null}
          {selectedSession || selectedGroup ? (
            /* 气泡锚点：包住分享图标的空 span（SharePopover 经 virtualRef 定位其下）。 */
            <span ref={shareAnchorRef} className="flex shrink-0">
              <HeaderIcon
                label={selectedSession ? '分享会话' : '分享协作群'}
                icon={<Share2 className="size-4" aria-hidden />}
                active={shareOpen}
                onClick={selectedSession ? handleShareSession : handleShareGroup}
              />
            </span>
          ) : null}
          {selectedSession && selectedGroup ? (
            <HeaderIcon
              label={activePanel === 'sessionManage' ? '关闭会话管理' : '管理会话'}
              icon={<SlidersHorizontal className="size-4" aria-hidden />}
              active={activePanel === 'sessionManage'}
              onClick={() => onTogglePanel(activePanel === 'sessionManage' ? 'none' : 'sessionManage')}
            />
          ) : selectedGroup ? (
            <HeaderIcon
              label={activePanel === 'manage' ? '关闭管理面板' : '管理协作群'}
              icon={<Settings2 className="size-4" aria-hidden />}
              active={activePanel === 'manage'}
              onClick={() => onTogglePanel(activePanel === 'manage' ? 'none' : 'manage')}
            />
          ) : null}
          {selectedSession ? (
            <HeaderIcon
              label={activePanel === 'history' ? '关闭历史消息' : '历史消息'}
              icon={<History className="size-4" aria-hidden />}
              active={activePanel === 'history'}
              onClick={() => onTogglePanel(activePanel === 'history' ? 'none' : 'history')}
            />
          ) : null}
          {selectedSession ? (
            <HeaderIcon
              label={activePanel === 'resources' ? '关闭资源管理' : '资源管理'}
              icon={<FolderOpen className="size-4" aria-hidden />}
              active={activePanel === 'resources'}
              onClick={() => onTogglePanel(activePanel === 'resources' ? 'none' : 'resources')}
            />
          ) : null}
          <HeaderIcon
            label="副屏"
            icon={<Monitor className="size-4" aria-hidden />}
            active={sidePaneOpen}
            disabled={sidePaneDisabled}
            onClick={onToggleSidePane}
          />
        </div>
      </header>
      <SharePopover
        open={shareOpen}
        anchorRef={shareAnchorRef}
        title={shareTarget === 'session' ? '会话' : '协作群'}
        onCreate={(expiresInSeconds) =>
          shareTarget === 'session' ? onRequestShareSession(expiresInSeconds) : onRequestShareGroup(expiresInSeconds)
        }
        onClose={() => setShareOpen(false)}
      />
    </>
  );
}

export default GroupHeader;