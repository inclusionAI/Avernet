// ChatPanel 宿主契约集中地：Props 接口承载会话接线 / 消息状态 / 头部插槽 / 副屏联动
// 四组入参，行数远超主组件本体；独立成契约文件下沉至同目录分层，使主组件
// 保持在工程红线（Component 300 行）之内。契约与装配逻辑同目录就近维护。
import type { IdentityView } from '@/domain/collaboration';
import type { ConversationTarget, SupportChatState } from '@/services/workspace/workspaceModel';
import type { ProviderConnectionStatus } from '@tc-chat/adapters';
import type { ChatBridge, ChatMessage, PanelAction, PanelHandle } from '@tc-chat/core';
import type { CommandConfig, FileChipConfig, SenderRef, SubmitContext } from '@tc-chat/ui/es/Sender';
import type { ReactNode, RefObject } from 'react';

export interface ChatPanelProps {
  target: ConversationTarget | null;
  /** 当前查看身份，用于在消息区展示真实发送者名称，避免使用有歧义的「你」。 */
  viewer?: IdentityView | null;
  /** 当前认证 human，用于按 message senderId 决定用户消息名称。 */
  authenticatedUserId?: string | null;
  authenticatedUserName?: string | null;
  /** 顶栏当前登录用户头像；用户消息优先复用此头像，与 Bot 消息保持区分。 */
  userAvatarUrl?: string;
  messages: ChatMessage[];
  isRequesting: boolean;
  isLoadingMessages: boolean;
  connectionStatus: ProviderConnectionStatus;
  retryCount: number;
  supportState: SupportChatState;
  draft: string;
  panelRef: RefObject<PanelHandle>;
  /** 主→副事件通道桥（经 <ChatLayout.Panel bridge=...> 注入；不传则不接主→副事件）。 */
  chatBridge?: ChatBridge;
  onDraftChange: (content: string) => void;
  onSend: (content: string, context?: SubmitContext) => void;
  onStop: () => void;
  onReconnect: () => void;
  onPanelAction: (action: PanelAction) => void;
  modelSelector?: ReactNode;
  fileChip?: FileChipConfig;
  command?: CommandConfig;
  composerHeader?: ReactNode;
  fileToolbar?: ReactNode;
  senderRef?: React.RefObject<SenderRef | null>;
  mode?: 'demo' | 'support' | 'bot';
  interactive?: boolean;
  /** 任务发起入口（单 Bot / 协作群共用）。仅 bot 模式渲染。 */
  taskComposer?: ReactNode;
  /**
   * 主屏输入框 ref（绑定原生 <Sender ref>）。透传自 useWorkspace.inputRef，经 useChatBridge.setInputRef
   * 注册到全局桥，使 aixcore 卡片 bridge.getInputRef().insert(text) 填入单聊主屏输入框并聚焦（根因 5 修复）。
   * SenderRef 是 BridgeInputRef 超集；原生 Sender 是 forwardRef（ChatLayout.Sender 非 forwardRef,ref 恒 null）。
   */
  inputRef?: RefObject<SenderRef>;
  /** 当前会话标题（单聊选中会话后顶栏显示会话名，与协作群顶栏一致；缺省回退 target.name）。 */
  sessionTitle?: string;
  /** 当前会话 ID（SVG 稿头部会话码胶囊，纯展示）。 */
  sessionCode?: string;
  /** <lg 打开单聊会话列表。 */
  onOpenSessionList?: () => void;
  historyPagination?: { hasMore: boolean; isLoading: boolean; onLoadMore: () => void };
  /** 打开会话文件面板（验收微调：文件管理入口迁至顶栏，与协作群位置规则一致；缺省不渲染入口）。 */
  onManageFiles?: () => void;
  /** 对话页头部图标列插槽（chat-header-panels）：提供时取代文件入口图标位，行为由页面装配。 */
  headerActions?: ReactNode;
  /** 待定位高亮的消息 id（历史面板结果点击）；由消息列表滚动至该消息并短暂高亮。 */
  highlightMessageId?: string | null;
  /** 副屏开合态（chat-header-panels，真实态同步）：开时渲染副屏右上「收起」悬浮钮。 */
  sidePaneOpen?: boolean;
  /** 副屏开合 toggle（chat-header-panels）：开时悬浮钮点按 = 关闭。 */
  onToggleSidePane?: () => void;
  /** 副屏真实开合态同步入口（chat-header-panels）：接线 ChatLayoutPanelSlot onOpenStateChange。 */
  onSidePaneIsOpenChange?: (open: boolean) => void;
}