// useConversationInteractiveChat —— 交互式(Human-facing)主舞台模型 Hook。
// 从旧混合 Workspace 页抽出的可交互单聊装配:任务发起/卡片执行、发送拦截(首条消息
// 自动重命名 + 文件引用)、会话文件能力(上传/管理/引用 + /clear /skill)、模型切换。
// 纯装配收口:不读 workspaceStore.activeIdentityId,登录 Human 的目标/会话/身份由
// 页边界(Task 8 ConversationPage)提供;组件层只消费这里的模型。
import type { IdentityView } from '@/domain/collaboration/types';
import { buildSingleChatBridgeRequest } from '@/hooks/singleChatBridgeRequest';
import { useComposerSend } from '@/hooks/useComposerSend';
import { useTaskExecuteFromCard } from '@/hooks/useTaskExecuteFromCard';
import { useTaskExecution, type UseTaskExecutionResult } from '@/hooks/useTaskExecution';
import { useTaskPreflightAssistant } from '@/hooks/useTaskPreflightAssistant';
import { useBotChat } from '@/pages/Workspace/hooks/useBotChat';
import {
  useBotSessionFilesFeature,
  type UseBotSessionFilesFeatureResult,
} from '@/pages/Workspace/hooks/useBotSessionFilesFeature';
import type { TaskComposerContext } from '@/services/tasks/taskMapper';
import type { BotChatSessionView, ChatBotView } from '@/services/workspace/botSessionService';
import { resolveUserId } from '@/services/workspace/botSessionService';
import { chatBridge } from '@/services/workspace/chatBridge';
import type { ConversationTarget } from '@/services/workspace/workspaceModel';
import type { ChatBridge, PanelAction, PanelHandle, ResourceReference } from '@tc-chat/core';
import type { SenderRef, SubmitContext } from '@tc-chat/ui/es/Sender';
import { useCallback, useEffect, useMemo, useState, type RefObject } from 'react';

/** 交互式主舞台依赖的既有会话操作(来自 useBotSessions,按结构最小面收窄)。 */
export interface ConversationInteractiveSessionOperations {
  renameSessionOnFirstMessage(bot: ChatBotView, session: BotChatSessionView, content: string): Promise<boolean>;
  clearContext(bot: ChatBotView, sessionId: string): Promise<boolean>;
  updateSessionModel(botId: string, sessionId: string, model: string): void;
}

export interface UseConversationInteractiveChatOptions {
  /** Human-facing 目标 Bot(ChatPanel 顶栏等)。 */
  target: ConversationTarget | null;
  /** 当前查看者(登录 Human)身份视图。 */
  viewer: IdentityView | null;
  /** 登录用户认证信息(ChatPanel 用户消息名称/头像)。 */
  authenticatedUserId: string | null | undefined;
  authenticatedUserName: string | null | undefined;
  authenticatedUserAvatarUrl: string | undefined;
  /** 请求身份 ID(任务 ownerUserId 源 / 会话文件 user_id / 模型切换接口)。 */
  requesterIdentityId: string | null;
  /** 全量可单聊 Bot(管理 + 好友,模型选择器用)。 */
  chatBots: ChatBotView[];
  /** 当前选中的「我发起」会话。 */
  selectedSession: BotChatSessionView | null;
  /** 既有 bot 单聊对话 Hook(WebSocket/历史分页/发送)。 */
  botChat: ReturnType<typeof useBotChat>;
  panelRef: RefObject<PanelHandle | null>;
  /** 主屏输入框 ref(页面持有;桥注册在页面/旧 useWorkspace 内)。 */
  inputRef: RefObject<SenderRef | null>;
  /** Agent Coding 引导目标;存在时舞台以引导替代 ChatPanel。 */
  selectedAgentCodingBot: ChatBotView | null;
  /** 既有会话操作(重命名/清除上下文/模型切换)。 */
  sessions: ConversationInteractiveSessionOperations;
  /** 副屏面板非填充动作的发送出口;缺省按旧页行为(单聊不处理)。 */
  onPanelSend?: (content: string) => void;
}

export interface ConversationInteractiveChatModel {
  target: ConversationTarget | null;
  viewer: IdentityView | null;
  selectedSession: BotChatSessionView | null;
  botChat: ReturnType<typeof useBotChat>;
  panelRef: RefObject<PanelHandle | null>;
  /** 登录用户认证信息(ChatPanel 用户消息名称/头像)。 */
  authenticatedUserId: string | null | undefined;
  authenticatedUserName: string | null | undefined;
  userAvatarUrl: string | undefined;
  /** Agent Coding 引导目标;存在时舞台渲染引导而非 ChatPanel。 */
  agentCodingBot: ChatBotView | null;
  draft: string;
  onDraftChange: (draft: string) => void;
  handleSend: (content: string, context?: SubmitContext) => Promise<void>;
  handlePanelAction: (action: PanelAction) => void;
  chatBridge: ChatBridge;
  inputRef: RefObject<SenderRef | null>;
  /** 任务发起编排(+ 号菜单装配)。 */
  taskExecution: UseTaskExecutionResult;
  taskComposerDisabled: boolean;
  taskComposerDisabledReason: string | null;
  /** 模型选择器装配(全量 Bot + 请求身份)。 */
  chatBots: ChatBotView[];
  requesterIdentityId: string | null;
  onSessionModelChange: (botId: string, sessionId: string, model: string) => void;
  /** 会话文件能力(上传/管理/引用 + 命令)。 */
  fileFeature: UseBotSessionFilesFeatureResult;
  /** desktop Bot 无本机文件能力:上传/文件管理/引用入口一并隐藏(desktop-web-migration)。 */
  desktopFileFeaturesHidden: boolean;
}

/**
 * 组装交互式单聊主舞台所需模型。仅做装配:目标/会话/身份由调用方(页边界)注入,
 * 与旧 Workspace 页的完整 ChatPanel 能力一一对应(发送/任务/文件/模型/草稿/副屏桥)。
 */
export function useConversationInteractiveChat(
  options: UseConversationInteractiveChatOptions,
): ConversationInteractiveChatModel {
  const { target, viewer, selectedSession, botChat, panelRef, inputRef, sessions } = options;

  const [draft, setDraft] = useState('');
  // 切换目标即清空草稿(对齐旧 Workspace 页 activeTargetId 清空逻辑)。
  useEffect(() => {
    setDraft('');
  }, [target?.id]);

  const selectedChatBot = useMemo(
    () => (selectedSession ? options.chatBots.find((bot) => bot.botId === selectedSession.botId) ?? null : null),
    [options.chatBots, selectedSession],
  );

  // desktop Bot 无本机文件能力:上传/文件管理/引用入口整体隐藏(desktop-web-migration,沿 upstream 旧页条件)。
  const desktopFileFeaturesHidden = selectedChatBot?.botType === 'desktop';

  // 真实用户 Bot 单聊:任务发起上下文(owner/session)。会话文件/模型接口沿用请求身份 ID。
  const taskOwnerUserId = options.requesterIdentityId ? resolveUserId(options.requesterIdentityId) : '';
  const taskComposerContext = useMemo<TaskComposerContext | null>(() => {
    if (viewer?.kind === 'bot') return null;
    const ownerBotId = selectedSession?.botId ?? target?.id;
    if (!ownerBotId || !options.requesterIdentityId || !taskOwnerUserId) return null;
    return {
      sourceType: 'bot',
      ownerUserId: taskOwnerUserId,
      ownerBotId,
      mainSessionId: selectedSession?.sessionId,
      mainSessionName: selectedSession?.title,
      parentTaskId: null,
    };
  }, [viewer?.kind, target?.id, options.requesterIdentityId, selectedSession, taskOwnerUserId]);

  const submitPanelMessage = useCallback(
    (content: string) => {
      botChat.chat.onRequest(buildSingleChatBridgeRequest(target?.id ?? null, content));
    },
    [botChat.chat, target?.id],
  );

  const taskExecution = useTaskExecution({
    panelRef,
    context: taskComposerContext,
    submitPanelMessage,
  });

  // 卡片「执行」按钮拦截:task_ready 点执行 → execute + 本地插 panel 消息 → 副屏持久。
  const { appendAssistantMessage, streamAssistantMessage } = useTaskPreflightAssistant({
    chat: botChat.chat,
    sessionKey: selectedSession?.sessionId,
  });
  useTaskExecuteFromCard({
    panelRef,
    context: taskComposerContext,
    submitPanelMessage,
    appendAssistantMessage,
    streamAssistantMessage,
  });

  const handleSend = useComposerSend(taskExecution, {
    beforeSend: async (content) => {
      if (!selectedChatBot || !selectedSession) return;
      await sessions.renameSessionOnFirstMessage(selectedChatBot, selectedSession, content);
    },
    sendMessage: (content, context) => {
      const refs = context?.fileRefs;
      if (refs && refs.length > 0) {
        botChat.send(context?.resolvedContent ?? content, {
          resourceReferences: refs.map(
            (f): ResourceReference => ({ type: 'file', resource_id: f.resource_id, insert_id: f.insert_id }),
          ),
          promptFileRefs: refs.map((f) => ({ resource_id: f.resource_id, insert_id: f.insert_id })),
          fileRefDisplay: refs.map((f) => ({ insert_id: f.insert_id, name: f.display_name })),
        });
      } else {
        botChat.send(content);
      }
    },
    clearDraft: () => setDraft(''),
  });

  const handleClearContext = useCallback(async () => {
    if (selectedChatBot && selectedSession) {
      await sessions.clearContext(selectedChatBot, selectedSession.sessionId);
      botChat.reloadHistory();
    }
  }, [selectedChatBot, selectedSession, sessions, botChat]);

  const fileFeature = useBotSessionFilesFeature(
    selectedChatBot,
    selectedSession,
    options.requesterIdentityId,
    handleClearContext,
  );

  const handlePanelAction = useCallback(
    (action: PanelAction) => {
      if (action.type === 'fill_input') {
        setDraft(action.content);
        return;
      }
      options.onPanelSend?.(action.content);
    },
    [options.onPanelSend],
  );

  return {
    target,
    viewer,
    selectedSession,
    botChat,
    panelRef,
    authenticatedUserId: options.authenticatedUserId,
    authenticatedUserName: options.authenticatedUserName,
    userAvatarUrl: options.authenticatedUserAvatarUrl,
    agentCodingBot: options.selectedAgentCodingBot,
    draft,
    onDraftChange: setDraft,
    handleSend,
    handlePanelAction,
    chatBridge,
    inputRef,
    taskExecution,
    taskComposerDisabled: !taskComposerContext,
    taskComposerDisabledReason: !taskComposerContext ? '请先选择一个 Bot 会话' : null,
    chatBots: options.chatBots,
    requesterIdentityId: options.requesterIdentityId,
    onSessionModelChange: sessions.updateSessionModel,
    fileFeature,
    desktopFileFeaturesHidden,
  };
}
