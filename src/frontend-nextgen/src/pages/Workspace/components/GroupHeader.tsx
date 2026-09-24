import { IconButton } from '@/components/ui';
import type { GroupView, SessionView } from '@/domain/collaboration';
import type { PolicyResult } from '@/services/workspace/groupService';
import type { DomainResult } from '@/services/workspace/identityService';
import type { ProviderConnectionStatus } from '@tc-chat/adapters';
import { ChevronRight, FolderOpen, RefreshCw, Settings2, Share2 } from 'lucide-react';
import { useState } from 'react';
import { KIND_LABEL } from './GroupSidebar/GroupItem.types';
import { ShareDialog } from './ManagePanel/ShareDialog';

export type GroupPanelKind = 'none' | 'members' | 'manage' | 'sessionManage' | 'resources';

export interface GroupHeaderProps {
  selectedGroup: GroupView | null;
  selectedSession: SessionView | null;
  connectionStatus: ProviderConnectionStatus;
  onReconnect: () => void;
  onOpenSessionList?: () => void;
  canManageGroup: PolicyResult;
  activePanel: GroupPanelKind;
  onTogglePanel: (panel: GroupPanelKind) => void;
  onRequestDissolve: () => void;
  onRequestShareGroup: () => Promise<DomainResult<{ invitationUrl: string }>>;
  onRequestShareSession: () => Promise<DomainResult<{ invitationUrl: string }>>;
}

/** 连接状态文字色：保留色调语义，去除胶囊底（轻文字与顶栏风格一致）。 */
const CONNECTION_TONE_TEXT: Record<'success' | 'warning' | 'error' | 'neutral', string> = {
  success: 'text-success',
  warning: 'text-warning',
  error: 'text-destructive',
  neutral: 'text-muted-foreground',
};

/**
 * 连接状态文案映射：输入为 useSessionDisplayStatus 合成显示语义（Spec:
 * docs/specs/workspace-session-connection-display.md），不再消费业务 phase——
 * 「准备中 / 加载历史」等实现细节在合成层已收敛为 connecting，顶栏无 phase 分支。
 */
function connectionCopy(status: ProviderConnectionStatus) {
  switch (status) {
    case 'connected':
      return { label: '已连接', tone: 'success' as const };
    case 'connecting':
      return { label: '连接中', tone: 'warning' as const };
    case 'reconnecting':
      return { label: '重连中', tone: 'warning' as const };
    case 'error':
      return { label: '连接失败', tone: 'error' as const };
    default:
      return { label: '已断开', tone: 'neutral' as const };
  }
}

export function GroupHeader({
  selectedGroup,
  selectedSession,
  connectionStatus,
  onReconnect,
  onOpenSessionList,
  activePanel,
  onTogglePanel,
  onRequestShareGroup,
  onRequestShareSession,
}: GroupHeaderProps) {
  const [shareOpen, setShareOpen] = useState(false);
  const [shareUrl, setShareUrl] = useState<string | null>(null);
  const [sharing, setSharing] = useState(false);
  const [shareTitle, setShareTitle] = useState('会话');
  const copy = connectionCopy(connectionStatus);
  const showReconnect =
    connectionStatus === 'disconnected' || connectionStatus === 'error' || connectionStatus === 'reconnecting';
  const memberCount = selectedGroup?.participants?.length || selectedGroup?.participantCount || 0;
  const subtitleLabel = [
    selectedSession && selectedGroup ? selectedGroup.name : null,
    memberCount > 0 ? `${memberCount} 个成员` : null,
    selectedGroup ? KIND_LABEL[selectedGroup.kind] : null,
  ]
    .filter(Boolean)
    .join(' · ');

  const handleShare = async (title: string, request: () => Promise<DomainResult<{ invitationUrl: string }>>) => {
    setShareOpen(true);
    setShareTitle(title);
    setSharing(true);
    setShareUrl(null);
    const res = await request();
    setSharing(false);
    if (res.ok) setShareUrl(res.data.invitationUrl);
  };

  const handleShareSession = () => handleShare('会话', onRequestShareSession);
  const handleShareGroup = () => handleShare('协作群', onRequestShareGroup);

  return (
    <>
      <header className="flex h-16 items-center gap-3 border-b border-border bg-card px-5">
        {onOpenSessionList ? (
          <IconButton
            label="打开会话列表"
            icon={<ChevronRight className="h-5 w-5" aria-hidden />}
            size="sm"
            className="shrink-0 lg:hidden"
            onClick={onOpenSessionList}
          />
        ) : null}
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h2 className="m-0 truncate text-sm font-semibold text-foreground">
              {selectedSession?.title ?? selectedGroup?.name ?? '未选择协作群'}
            </h2>
            {/* v1.4：连接状态不收缩不换行——超长标题截断由 h2 独占，状态恒一行。 */}
            <span className={`shrink-0 whitespace-nowrap text-xs ${CONNECTION_TONE_TEXT[copy.tone]}`}>
              {copy.label}
            </span>
          </div>
          <p className="m-0 mt-0.5 truncate text-xs text-muted-foreground">{subtitleLabel}</p>
        </div>

        <div className="flex items-center gap-1">
          {selectedSession && (
            <IconButton
              label={activePanel === 'resources' ? '关闭会话文件' : '会话文件'}
              icon={<FolderOpen className="h-4 w-4" aria-hidden />}
              size="sm"
              variant={activePanel === 'resources' ? 'primary' : 'ghost'}
              onClick={() => onTogglePanel(activePanel === 'resources' ? 'none' : 'resources')}
            />
          )}
          {selectedSession && (
            <IconButton
              label="分享会话"
              icon={<Share2 className="h-4 w-4" aria-hidden />}
              size="sm"
              variant="ghost"
              onClick={() => void handleShareSession()}
            />
          )}
          {!selectedSession && selectedGroup && (
            <IconButton
              label="分享协作群"
              icon={<Share2 className="h-4 w-4" aria-hidden />}
              size="sm"
              variant="ghost"
              onClick={() => void handleShareGroup()}
            />
          )}
          {/* 管理按钮：选中会话时进入「会话管理」(对会话成员可见,与侧栏会话「…」菜单一致);
              未选会话时回退「协作群管理」——群详情（含权限判定）在打开管理面板时按需拉取，
              非管理者打开后展示只读视图，避免选中群即触发 GET /groups/{id} 详情请求。 */}
          {selectedSession && selectedGroup ? (
            <IconButton
              label={activePanel === 'sessionManage' ? '关闭会话管理' : '管理会话'}
              icon={<Settings2 className="h-4 w-4" aria-hidden />}
              size="sm"
              variant={activePanel === 'sessionManage' ? 'primary' : 'ghost'}
              onClick={() => onTogglePanel(activePanel === 'sessionManage' ? 'none' : 'sessionManage')}
            />
          ) : selectedGroup ? (
            <IconButton
              label={activePanel === 'manage' ? '关闭管理面板' : '管理协作群'}
              icon={<Settings2 className="h-4 w-4" aria-hidden />}
              size="sm"
              variant={activePanel === 'manage' ? 'primary' : 'ghost'}
              onClick={() => onTogglePanel(activePanel === 'manage' ? 'none' : 'manage')}
            />
          ) : null}
          {showReconnect && (
            <IconButton
              label="点击重新连接"
              icon={<RefreshCw className="h-4 w-4" aria-hidden />}
              size="sm"
              variant="ghost"
              onClick={onReconnect}
            />
          )}
        </div>
      </header>
      <ShareDialog
        open={shareOpen}
        title={shareTitle}
        inviting={sharing}
        invitationUrl={shareUrl}
        onClose={() => setShareOpen(false)}
      />
    </>
  );
}

export default GroupHeader;
