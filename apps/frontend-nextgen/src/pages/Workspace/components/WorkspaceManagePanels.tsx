import type { DeliveryPolicy, GroupView, IdentityView, SessionView } from '@/domain/collaboration';
import type { DingTalkBindingState } from '@/services/workspace/channelBindingService';
import type { PolicyResult } from '@/services/workspace/groupService';
import type { DomainResult } from '@/services/workspace/identityService';
import { useEffect } from 'react';
import type { GroupPanelKind } from './GroupHeader';
import type { GroupDingTalkConfig } from './ManagePanel/DingTalkConfigPanel';
import { GroupManagePanel } from './ManagePanel/GroupManagePanel';
import { SessionManagePanel } from './ManagePanel/SessionManagePanel';
import { ResizableWorkspaceSidebar } from './ResizableWorkspaceSidebar';

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

  useEffect(() => {
    if (activePanel !== 'manage' && activePanel !== 'sessionManage') return;

    const handlePointerDown = (event: PointerEvent) => {
      const target = event.target as Element | null;
      if (!target?.closest) return;
      if (
        target.closest(
          '[data-manage-panel="true"], [data-manage-panel-trigger], [role="dialog"], [role="menu"], [role="alertdialog"]',
        )
      ) {
        return;
      }
      onClose();
    };

    document.addEventListener('pointerdown', handlePointerDown);
    return () => document.removeEventListener('pointerdown', handlePointerDown);
  }, [activePanel, onClose]);

  if (!group) return null;

  // 不渲染全屏点击层，避免遮挡页面交互；点击管理面板外部区域自动收起，
  // 面板内部、管理入口及浮层交互均保持打开。
  // 导致群列表与会话框无法上下滚动。面板改为与 members 面板一致的纯 inline 侧栏;
  // 关闭由 ManagePanelHeader 的 X 按钮 / GroupHeader 齿轮 toggle 承担。
  // （验收微调：移除 PR 364 引入的「点击面板外部自动收起」全局 pointerdown 监听，
  //   避免双击消息流选中文本时右侧副屏被意外收起；副屏作为常驻工作区只走显式关闭。）
  return (
    <>
      {activePanel === 'manage' ? (
        <ResizableWorkspaceSidebar
          ariaLabel="群管理面板"
          data-manage-panel="true"
          side="right"
          minWidth={320}
          maxWidth={600}
          defaultWidth={380}
          storageKey="teamclaw:manage-panel-width"
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
        </ResizableWorkspaceSidebar>
      ) : null}

      {activePanel === 'sessionManage' && session ? (
        <ResizableWorkspaceSidebar
          ariaLabel="会话管理面板"
          data-manage-panel="true"
          side="right"
          minWidth={320}
          maxWidth={600}
          defaultWidth={380}
          storageKey="teamclaw:manage-panel-width"
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
        </ResizableWorkspaceSidebar>
      ) : null}
    </>
  );
}
