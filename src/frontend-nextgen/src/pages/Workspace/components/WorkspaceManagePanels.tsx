import type { DeliveryPolicy, GroupView, IdentityView, SessionView } from '@/domain/collaboration';
import type { DingTalkBindingState } from '@/services/workspace/channelBindingService';
import type { PolicyResult } from '@/services/workspace/groupService';
import type { DomainResult } from '@/services/workspace/identityService';
import { useEffect } from 'react';
import type { GroupPanelKind } from './GroupHeader';
import type { GroupDingTalkConfig } from './ManagePanel/DingTalkConfigPanel';
import { GroupManagePanel } from './ManagePanel/GroupManagePanel';
import { SessionManagePanel } from './ManagePanel/SessionManagePanel';

export interface WorkspaceManagePanelsProps {
  activePanel: GroupPanelKind;
  group: GroupView | null;
  session: SessionView | null;
  groupAdvancedConfigEnabled: boolean;
  canManage: PolicyResult;
  identities: IdentityView[];
  activeIdentity: IdentityView | null;
  authenticatedUserId?: string | null;
  authenticatedUserName?: string | null;
  onClose: () => void;
  onUpdateGroup: (patch: {
    name?: string;
    visibility?: 'private' | 'public';
    deliveryPolicy?: DeliveryPolicy;
  }) => Promise<DomainResult<GroupView> | null> | void;
  onDissolveGroup: () => void;
  onLeaveGroup: (actorId: string) => Promise<boolean>;
  onAddGroupMember: (actorId: string) => Promise<boolean>;
  onRemoveGroupMember: (actorId: string) => Promise<boolean>;
  onShareGroup: () => Promise<DomainResult<{ invitationUrl: string }>>;
  onSaveDingTalk: (config: GroupDingTalkConfig) => Promise<boolean>;
  onToggleDingTalkActive: (active: boolean) => Promise<boolean>;
  onDeleteDingTalk: () => Promise<boolean>;
  dingTalkBinding: DingTalkBindingState;
  dingTalkLoading: boolean;
  onRenameSession: (sessionId: string, title: string) => Promise<boolean>;
  onDeleteSession: (sessionId: string) => Promise<boolean>;
  onLeaveSession: (actorId: string) => Promise<boolean>;
  onAddSessionMember: (actorId: string) => Promise<boolean>;
  onRemoveSessionMember: (actorId: string) => Promise<boolean>;
  onShareSession: () => Promise<DomainResult<{ invitationUrl: string }>>;
}

/** 右侧群/会话管理面板渲染：只负责 UI 编排，写操作由父级 Hook 提供。 */
export function WorkspaceManagePanels(props: WorkspaceManagePanelsProps) {
  const {
    activePanel,
    group,
    session,
    canManage,
    identities,
    activeIdentity,
    authenticatedUserId,
    authenticatedUserName,
    onClose,
  } = props;

  // 点击面板外部自动收起：监听 pointerdown，命中面板本体、面板开关按钮（data-manage-panel-trigger）
  // 或浮层（菜单/弹窗，避免侧栏「…」菜单打开面板时误关）之外的区域即关闭。
  useEffect(() => {
    if (activePanel !== 'manage' && activePanel !== 'sessionManage') return;
    const handlePointerDown = (event: PointerEvent) => {
      const target = event.target as Element | null;
      if (!target?.closest) return;
      if (
        target.closest('[data-manage-panel="true"]') ||
        target.closest('[data-manage-panel-trigger]') ||
        target.closest('[role="dialog"], [role="menu"], [role="alertdialog"]')
      ) {
        return;
      }
      onClose();
    };
    document.addEventListener('pointerdown', handlePointerDown);
    return () => document.removeEventListener('pointerdown', handlePointerDown);
  }, [activePanel, onClose]);

  if (!group) return null;

  // 不再渲染全屏点击层:fixed inset-0 的 backdrop 会盖在侧栏与聊天区之上,拦截 pointer/touch,
  // 导致群列表与会话框无法上下滚动。面板改为与 members 面板一致的纯 inline 侧栏,
  // 关闭由 ManagePanelHeader 的 X 按钮 / GroupHeader 齿轮 toggle / 点击面板外部（上方 effect）承担。
  return (
    <>
      {activePanel === 'manage' ? (
        <div
          data-manage-panel="true"
          className="relative z-30 flex w-[min(380px,36vw)] max-w-[36vw] shrink-0 border-l border-border"
        >
          <GroupManagePanel
            key={group.groupId}
            group={group}
            advancedConfigEnabled={props.groupAdvancedConfigEnabled}
            canManage={canManage}
            candidates={identities}
            activeIdentity={activeIdentity}
            authenticatedUserId={authenticatedUserId}
            authenticatedUserName={authenticatedUserName}
            onClose={onClose}
            onUpdate={props.onUpdateGroup}
            onDissolve={props.onDissolveGroup}
            onLeaveGroup={props.onLeaveGroup}
            onAddMember={props.onAddGroupMember}
            onRemoveMember={props.onRemoveGroupMember}
            onShare={props.onShareGroup}
            onSaveDingTalk={props.onSaveDingTalk}
            onToggleDingTalkActive={props.onToggleDingTalkActive}
            onDeleteDingTalk={props.onDeleteDingTalk}
            dingTalkBinding={props.dingTalkBinding}
            dingTalkLoading={props.dingTalkLoading}
          />
        </div>
      ) : null}

      {activePanel === 'sessionManage' && session ? (
        <div
          data-manage-panel="true"
          className="relative z-30 flex w-[min(380px,36vw)] max-w-[36vw] shrink-0 border-l border-border"
        >
          <SessionManagePanel
            key={session.sessionId}
            session={session}
            groupName={group.name}
            groupKind={group.kind}
            canManage={canManage}
            activeIdentity={activeIdentity}
            authenticatedUserId={authenticatedUserId}
            authenticatedUserName={authenticatedUserName}
            candidates={identities.filter((identity) => identity.kind === 'bot')}
            onClose={onClose}
            onRename={props.onRenameSession}
            onDelete={props.onDeleteSession}
            onLeaveSession={props.onLeaveSession}
            onAddMember={props.onAddSessionMember}
            onRemoveMember={props.onRemoveSessionMember}
            onShare={props.onShareSession}
          />
        </div>
      ) : null}
    </>
  );
}
