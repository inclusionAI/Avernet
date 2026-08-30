import { Badge, IconButton } from '@/components/ui';
import type { GroupView, SessionView } from '@/domain/collaboration';
import type { GroupChatState } from '@/services/workspace/groupChatProvider';
import type { PolicyResult } from '@/services/workspace/groupService';
import type { DomainResult } from '@/services/workspace/identityService';
import type { ProviderConnectionStatus } from '@tc-chat/adapters';
import { FolderOpen, RefreshCw, Settings2, Share2 } from 'lucide-react';
import { useState } from 'react';
import { ShareDialog } from './ManagePanel/ShareDialog';

export type GroupPanelKind = 'none' | 'members' | 'manage' | 'sessionManage' | 'resources';

export interface GroupHeaderProps {
  selectedGroup: GroupView | null;
  selectedSession: SessionView | null;
  supportState: GroupChatState;
  connectionStatus: ProviderConnectionStatus;
  onReconnect: () => void;
  canManageGroup: PolicyResult;
  activePanel: GroupPanelKind;
  onTogglePanel: (panel: GroupPanelKind) => void;
  onRequestDissolve: () => void;
  onRequestShareGroup: () => Promise<DomainResult<{ invitationUrl: string }>>;
  onRequestShareSession: () => Promise<DomainResult<{ invitationUrl: string }>>;
}

function connectionCopy(status: ProviderConnectionStatus, support: GroupChatState) {
  if (support.phase === 'preparing') return { label: '准备中', tone: 'warning' as const };
  if (support.phase === 'loading-history') return { label: '加载历史', tone: 'warning' as const };
  if (support.phase === 'error') return { label: '连接失败', tone: 'error' as const };
  switch (status) {
    case 'connected':
      return { label: '在线', tone: 'success' as const };
    case 'connecting':
      return { label: '连接中', tone: 'warning' as const };
    case 'reconnecting':
      return { label: '重连中', tone: 'warning' as const };
    case 'disconnected':
      return { label: '已断开', tone: 'neutral' as const };
    case 'error':
      return { label: '连接失败', tone: 'error' as const };
    default:
      return { label: '离线', tone: 'neutral' as const };
  }
}

export function GroupHeader({
  selectedGroup,
  selectedSession,
  supportState,
  connectionStatus,
  onReconnect,
  canManageGroup,
  activePanel,
  onTogglePanel,
  onRequestShareGroup,
  onRequestShareSession,
}: GroupHeaderProps) {
  const [shareOpen, setShareOpen] = useState(false);
  const [shareUrl, setShareUrl] = useState<string | null>(null);
  const [sharing, setSharing] = useState(false);
  const [shareTitle, setShareTitle] = useState('会话');
  const copy = connectionCopy(connectionStatus, supportState);
  const showReconnect =
    connectionStatus === 'disconnected' || connectionStatus === 'error' || connectionStatus === 'reconnecting';
  const memberCount = selectedGroup?.participants?.length ?? 0;

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
      <header className="flex h-16 items-center gap-3 border-b border-[var(--color-border)] bg-white px-5">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h2 className="m-0 truncate text-sm font-semibold text-[var(--color-fg)]">
              {selectedGroup?.name ?? '未选择协作群'}
            </h2>
            <Badge tone={copy.tone}>{copy.label}</Badge>
            {selectedSession && (
              <span className="truncate text-xs text-[var(--color-muted)]"># {selectedSession.title}</span>
            )}
          </div>
          <p className="m-0 mt-0.5 truncate text-xs text-[var(--color-muted)]">
            {memberCount > 0 ? `${memberCount} 位成员` : '协作群 · 群组对话'}
          </p>
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
              未选会话时回退「协作群管理」(仅群管理者可见)。避免选中会话后仍误开群编辑面板。 */}
          {selectedSession && selectedGroup ? (
            <IconButton
              label={activePanel === 'sessionManage' ? '关闭会话管理' : '管理会话'}
              icon={<Settings2 className="h-4 w-4" aria-hidden />}
              size="sm"
              variant={activePanel === 'sessionManage' ? 'primary' : 'ghost'}
              onClick={() => onTogglePanel(activePanel === 'sessionManage' ? 'none' : 'sessionManage')}
            />
          ) : canManageGroup.allowed && selectedGroup ? (
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
