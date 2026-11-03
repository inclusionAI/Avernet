import { WORKSPACE_RIGHT_PANEL_LAYOUT } from '@/components/Workspace/workspacePanelLayout';
import type { GroupUpdatePatch, GroupView, IdentityView, SessionView } from '@/domain/collaboration';
import type { DingTalkBindingState } from '@/services/workspace/channelBindingService';
import type { PolicyResult } from '@/services/workspace/groupService';
import type { DomainResult } from '@/services/workspace/identityService';
import type { GroupPanelKind } from './GroupHeader';
import type { GroupDingTalkConfig } from './ManagePanel/DingTalkConfigPanel';
import { GroupManagePanel } from './ManagePanel/GroupManagePanel';
import { SessionManagePanel } from './ManagePanel/SessionManagePanel';
import { WorkspaceOverlayPanel } from './WorkspaceOverlayPanel';

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
  onUpdateGroup: (patch: GroupUpdatePatch) => Promise<DomainResult<GroupView> | null> | void;
  onDissolveGroup: () => void;
  onLeaveGroup: (actorId: string) => Promise<boolean>;
  onAddGroupMember: (actorId: string) => Promise<boolean>;
  onRemoveGroupMember: (actorId: string) => Promise<boolean>;
  onShareGroup: (expiresInSeconds?: number) => Promise<DomainResult<{ invitationUrl: string }>>;
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
  onShareSession: (expiresInSeconds?: number) => Promise<DomainResult<{ invitationUrl: string }>>;
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

  if (!group) return null;

  // 会话局部遮罩由公共覆盖层处理，保留内部及 Portal 浮层交互。
  return (
    <>
      {activePanel === 'manage' ? (
        <WorkspaceOverlayPanel
          ariaLabel="群管理面板"
          data-manage-panel="true"
          onDismiss={onClose}
          {...WORKSPACE_RIGHT_PANEL_LAYOUT}
          className="z-30 bg-background"
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
        </WorkspaceOverlayPanel>
      ) : null}

      {activePanel === 'sessionManage' && session ? (
        <WorkspaceOverlayPanel
          ariaLabel="会话管理面板"
          data-manage-panel="true"
          onDismiss={onClose}
          {...WORKSPACE_RIGHT_PANEL_LAYOUT}
          className="z-30 bg-background"
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
        </WorkspaceOverlayPanel>
      ) : null}
    </>
  );
}
