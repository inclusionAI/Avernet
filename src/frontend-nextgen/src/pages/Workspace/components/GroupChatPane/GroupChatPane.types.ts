import type { GroupView, IdentityView, ParticipantMode, SessionView } from '@/domain/collaboration';
import type { SessionMessageAttachment } from '@/services/workspace/groupChatAttachmentService';
import type { GroupChatState } from '@/services/workspace/groupChatProvider';
import type { PolicyResult } from '@/services/workspace/groupService';
import type { DomainResult } from '@/services/workspace/identityService';
import type { ProviderConnectionStatus, UseChatResult } from '@tc-chat/adapters';
import type { ChatBridge, PanelHandle } from '@tc-chat/core';
import type { SenderRef } from '@tc-chat/ui/es/Sender';
import type { RefObject } from 'react';
import type { GroupPanelKind } from '../GroupHeader';

export interface GroupChatPaneProps {
  group: GroupView | null;
  session: SessionView | null;
  /** 当前浏览身份（决定是否渲染底部协作面板：bot 视角恒显，human absent 时显示加入条）。 */
  activeIdentity?: IdentityView | null;
  /** 会话成员 mode 更新出口（PATCH participants/{actor}），经由会话 Hook 注入。 */
  updateMemberMode?: (sessionId: string, actorId: string, mode: ParticipantMode) => Promise<boolean>;
  chat: UseChatResult<unknown>;
  /** 建群后 Driver/Manager 尚未产生第一条消息时的瞬态处理状态。 */
  groupBootstrapProcessing?: boolean;
  supportState: GroupChatState;
  connectionStatus: ProviderConnectionStatus;
  send: (text: string, mentions?: string[], attachments?: SessionMessageAttachment[]) => void;
  /** 按当前群会话直发副屏 <AixUI> 消息（绕开全局桥 last-wins）。由 useGroupChat 提供。 */
  submitPanelMessage: (content: string) => void;
  /** 演示用：本地追加 assistant 回复，不通过聊天网络请求。 */
  appendAssistantMessage?: (content: string) => void;
  /** 演示用：以流式方式追加 assistant 回复，不通过聊天网络请求。 */
  streamAssistantMessage?: (content: string) => Promise<void>;
  stop: () => void;
  reconnect: () => Promise<void> | void;
  /** 重新加载会话历史：error 状态下的「重新加载历史」走此出口（直连 provider.loadHistory）。 */
  reloadHistory: () => Promise<void> | void;
  /** 历史消息是否还有更早一页可加载（顶部「加载更多」显隐）。 */
  hasMoreHistory?: boolean;
  /** 是否正在向上翻页加载更早的历史消息（顶部加载指示器）。 */
  isLoadingMoreHistory?: boolean;
  /** 用户滚到顶部时触发加载更早的历史消息。 */
  onLoadMoreHistory?: () => void;
  canManageGroup: PolicyResult;
  activePanel: GroupPanelKind;
  onTogglePanel: (panel: GroupPanelKind) => void;
  onRequestDissolve: () => void;
  onRequestShareGroup: () => Promise<DomainResult<{ invitationUrl: string }>>;
  onRequestShareSession: () => Promise<DomainResult<{ invitationUrl: string }>>;
  /** 副屏命令式 handle（来自 useGroupChat，供 ChatLayout.Panel ref + closePanelForce）。 */
  panelRef: RefObject<PanelHandle>;
  /**
   * 主屏输入框 ref（绑定 <ChatLayout.Sender ref>）。经 useGroupChat → useChatBridge 注册到全局桥,
   * 使 aixcore 卡片 bridge.getInputRef().insert(text) 填入主屏输入框并聚焦（"填输入框"症状修复）。
   * SenderRef 是 BridgeInputRef 超集;Hook 层声明为 SenderRef,这里以 SenderRef 接收匹配 Sender ref 类型。
   */
  inputRef?: RefObject<SenderRef>;
  /** 顶栏当前登录用户头像；用户消息优先复用此头像。 */
  userAvatarUrl?: string;
  /** 当前登录用户身份 ID，用于保留其他 human 成员自己的头像。 */
  userIdentityId?: string | null;
  /** 主→副事件通道桥（经 <ChatLayout.Panel bridge=...> 注入；不传则不接主→副事件）。 */
  chatBridge?: ChatBridge;
}
