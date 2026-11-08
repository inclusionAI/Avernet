import { IconButton } from '@/components/ui';
import { ChatConnectionStatus } from '@/components/Workspace/ChatConnectionStatus';
import { PrimaryNavToggle } from '@/components/Workspace/PrimaryNav';
import type { GroupView, SessionView } from '@/domain/collaboration';
import type { GroupChatFavoritesModel } from '@/pages/Workspace/hooks/useGroupChatHeaderPanels';
import type { DomainResult } from '@/services/workspace/identityService';
import { cn } from '@/utils/cn';
import type { ProviderConnectionStatus } from '@tc-chat/adapters';
import {
  Bot,
  ChevronRight,
  FolderOpen,
  History,
  Monitor,
  Settings2,
  Share2,
  SlidersHorizontal,
  Star,
  Users,
} from 'lucide-react';
import { useRef, useState } from 'react';
import { GroupAvatarStack } from './GroupSidebar/GroupItem';
import { GroupSessionIdPill } from './GroupSessionIdPill';
import { SharePopover } from './ManagePanel/SharePopover';
import { Points } from '@/tracking/points';
import { Tracert } from '@/utils/tracert';

export type GroupPanelKind = 'none' | 'members' | 'manage' | 'sessionManage' | 'resources' | 'history';

const CONNECTION_COPY = {
  connected: { label: '已连接', tone: 'success' },
  connecting: { label: '连接中', tone: 'warning' },
  reconnecting: { label: '重连中', tone: 'warning' },
  error: { label: '连接失败', tone: 'error' },
  disconnected: { label: '已断开', tone: 'neutral' },
} as const;

export interface GroupHeaderProps {
  selectedGroup: GroupView | null;
  selectedSession: SessionView | null;
  connectionStatus: ProviderConnectionStatus;
  historyOnly?: boolean;
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
 * header chip（dmore artboard-003 群对话标题栏，48px 顶栏）：群身份 chip（头像堆/群名/会话 ID
 * 胶囊/用户与 Bot 计数）+ 右侧 28px 图标钮列。六槽位按设计稿 x=0/32/64/96/128/160 全量落地：
 * 收藏会话 / 分享 / 会话管理 / 历史消息 / 资源管理 / 副屏——其中五枚与 chat 页 ChatHeaderActions
 * 同构（Star/SlidersHorizontal/History/FolderOpen/Monitor），分享为协作群特有（SharePopover 气泡）。
 * 历史消息由右缘 GroupHistorySidebar 承载；顶栏连接状态与对话页同形，输入帽条保留重连操作。
 *
 * 窄宽让位（container query 量外层 wrapper 宽度而非 viewport，副屏/管理面板挤压主区时同样生效）：
 * ① 群名/ID 胶囊各自 min-w-0 + truncate，胶囊对齐 chat 行为（胶囊有 40px 内在地板，退化到仅复制钮）；
 * ② <32.5rem 收起次级成员徽标（管理面板仍可见）；③ <30rem 折成两行（身份行 + 图标行），
 * 图标钮与复制入口任何宽度不隐藏。header 自身不能查询自身，断点容器是外层命名 wrapper。
 * ④（2026-10-19 窄宽反馈）「已连接」文案与 ② 同断点收纳为仅绿点（视口 <lg 恒收纳）：
 * 视口与容器双闸经 ChatConnectionStatus 的 labelVisibleClassName（lg:@min-[32.5rem]/…）并查，
 * 正常态无需解释，警示/错误态保留完整文案与语义色，读屏名由 aria-label 恒撑。
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
      data-workspace-panel-trigger
      onClick={onClick}
      className={cn('rounded-md text-muted-foreground', active && 'bg-selected text-foreground', className)}
    />
  );
}

export function GroupHeader({
  historyOnly = false,
  selectedGroup,
  selectedSession,
  connectionStatus,
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

  // 窄屏会话列表入口（<lg viewport）：有群时排在 chip 前（进入身份行），无群时独踞左侧。
  // 一级导航入口（壳提供）恒在会话列表入口左侧同排——壳外/精简路由由上下文裁剪为 null。
  const openSessionListButton = onOpenSessionList ? (
    <IconButton
      label="打开会话列表"
      icon={<ChevronRight className="h-5 w-5" aria-hidden />}
      size="sm"
      className="shrink-0 lg:hidden"
      onClick={onOpenSessionList}
    />
  ) : null;

  return (
    <>
      {/* 命名查询容器：header 及其内部断点都量这一层宽度（element 不能查询自身）。 */}
      <div className="@container/group-header shrink-0">
        <header className="flex h-12 items-center gap-2 border-b border-border bg-card px-3 @max-[30rem]/group-header:h-auto @max-[30rem]/group-header:flex-wrap @max-[30rem]/group-header:py-2 sm:px-4">
          {selectedGroup ? (
            /* 极窄（<30rem）wrapper basis-full 独占首行（flex-1 基线 0 不触发换行），图标钮列换到次行。 */
            <div className="flex min-w-0 max-w-full flex-1 items-center gap-2 @max-[30rem]/group-header:basis-full">
              <PrimaryNavToggle />
              {openSessionListButton}
              <div
                role="group"
                className="flex h-8 min-w-0 items-center gap-2 rounded-[10px] border border-border bg-card px-2.5"
                aria-label={`协作群 ${selectedGroup.name}`}
              >
                <GroupAvatarStack participants={participants} totalCount={totalCount} />
                <span className="min-w-0 shrink truncate text-[13px] leading-[18px] text-content-strong">
                  {selectedGroup.name}
                </span>
                {selectedSession ? <GroupSessionIdPill sessionId={selectedSession.sessionId} /> : null}
                {participants.length > 0 || totalCount > 0 ? (
                  <span className="@max-[32.5rem]/group-header:hidden flex shrink-0 items-center gap-2">
                    <span aria-hidden className="h-3 w-px bg-foreground/10" />
                    <span className="flex items-center gap-1" aria-label={`${humans || totalCount} 用户`}>
                      <Users className="h-3 w-3 text-zinc-400" aria-hidden />
                      <span className="text-xs leading-4 text-zinc-400">{humans || totalCount}</span>
                    </span>
                    {bots > 0 ? (
                      <span className="flex items-center gap-1" aria-label={`${bots} Bot`}>
                        <Bot className="h-3 w-3 text-zinc-400" aria-hidden />
                        <span className="text-xs leading-4 text-zinc-400">{bots}</span>
                      </span>
                    ) : null}
                  </span>
                ) : null}
              </div>
              {selectedSession ? (
                <ChatConnectionStatus
                  {...(historyOnly
                    ? { label: selectedSession.participants.length ? '未加入当前会话' : '正在加载成员信息', tone: 'neutral' as const }
                    : CONNECTION_COPY[connectionStatus])}
                  // ④ 窄宽收纳（视口+容器双闸）：桌面主区被副屏/管理面板挤到 <32.5rem 时同样仅绿点。
                  labelVisibleClassName="lg:@min-[32.5rem]/group-header:inline"
                />
              ) : null}
            </div>
          ) : (
            <>
              <PrimaryNavToggle />
              {openSessionListButton}
            </>
          )}

          <div className="flex shrink-0 items-center justify-end gap-1">
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
                  onClick={() => {
                    if (!selectedSession) {
                      // 协作群分享=邀请入口(add-spm-tracking:协作群聚焦页-邀请成员)
                      Tracert.click(Points.groupFocus.inviteMember, { group_id: selectedGroup?.groupId });
                    }
                    if (selectedSession) handleShareSession();
                    else handleShareGroup();
                  }}
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
      </div>
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
