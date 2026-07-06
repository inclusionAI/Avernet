/**
 * GroupChatPage - 群聊聊天页面
 *
 * 使用 useGroupChat hook 管理群聊消息
 * 使用 MessageList 渲染消息列表
 * 使用 BottomPanel 渲染底部 Tab 布局（Bot控制 / 用户协作）
 */

import { useExt } from '@/capabilities';
import MessageList from '@/components/chat/MessageList';
import Empty from '@/components/Empty';
import MessageAvatar from '@/components/MessageAvatar';
import { queryAndRegisterManifestCdnPanels } from '@/components/Panels/registerBotCdnPanels';
import { registerPanel } from '@/components/Panels/registry';
import { UmdPanel } from '@/components/Panels/UmdPanel';
import { Skeleton } from '@/components/Skeleton';
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip';
import { AppExt } from '@/shell';
import { useBotNetworkStore } from '@/stores/botNetworkStore';
import { useUserStore } from '@/stores/userStore';
import { chatBridge } from '@/utils/chatBridge';
import type { ChatMessage } from '@aix-chat/core';
import type { MentionItem, PanelHandle } from '@aix-chat/ui';
import { ChatLayout, hasAixPanelContent } from '@aix-chat/ui';
import { ArrowLeft, Bot, Cpu, ShieldCheck, Users, VolumeX } from 'lucide-react';
import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { toast } from 'sonner';
import { useActor } from '../hooks/useActor';
import { useBotNetwork } from '../hooks/useBotNetwork';
import { useGroupChat } from '../hooks/useGroupChat';
import type {
  GroupInfo,
  GroupMessage,
  GroupSession,
  ParticipantMode,
} from '../types';
import BottomPanel from './BottomPanel/BottomPanel';
import SessionHeader from './SessionHeader';

registerPanel({ type: 'umd', name: 'UMD面板', component: UmdPanel });

interface GroupChatPageProps {
  /** 当前群组信息 */
  group: GroupInfo;
  /** 消息列表（历史消息） */
  messages: GroupMessage[];
  /** 是否正在加载消息 */
  isLoadingMessages: boolean;
  /** 重新拉取会话消息（手动重连时用后端最新历史补回实时消息） */
  onReloadMessages?: (sessionId: string) => void | Promise<void>;
  /** 是否为移动端（移动端隐藏内部 Header） */
  isMobile?: boolean;
  /** 群组数据刷新回调（加入/退出协作、mode 切换后调用） */
  onRefreshGroup?: () => void;
  /** 用户是否需要加入 BCN 网络（Human Actor 未初始化） */
  needsBcnInit?: boolean;
  /** 加入 BCN 网络回调 */
  onJoinBcn?: () => void;
  /** 是否正在加入 BCN 网络 */
  isJoiningBcn?: boolean;
  /** 当前活跃的会话（会话模式时传入） */
  activeSession?: GroupSession | null;
  /** 加入会话回调 */
  onJoinSession?: (sessionId: string, actorId: string) => Promise<boolean>;
  /** 退出会话回调 */
  onLeaveSession?: (sessionId: string, actorId: string) => Promise<boolean>;
  /** 更新会话标题回调 */
  onUpdateSessionTitle?: (sessionId: string, title: string) => Promise<boolean>;
  /** 是否正在更新会话标题 */
  isUpdatingSessionTitle?: boolean;
  /** 返回会话列表回调 */
  onBackToSessionList?: () => void;
  /** 更新会话成员模式回调（Bot 禁言/自动） */
  onUpdateSessionMemberMode?: (
    sessionId: string,
    actorId: string,
    mode: 'auto' | 'muted',
  ) => Promise<boolean>;
  /** 刷新当前会话详情回调（模式切换后调用） */
  onRefreshSession?: (sessionId: string) => Promise<void>;
}

const GroupChatPage: React.FC<GroupChatPageProps> = ({
  group,
  messages: historyMessages,
  isLoadingMessages,
  onReloadMessages,
  isMobile,
  onRefreshGroup,
  needsBcnInit,
  onJoinBcn,
  isJoiningBcn,
  activeSession,
  onJoinSession,
  onLeaveSession,
  onUpdateSessionTitle,
  isUpdatingSessionTitle,
  onBackToSessionList,
  onUpdateSessionMemberMode,
  onRefreshSession,
}) => {
  // 使用 useGroupChat hook
  const {
    messages,
    isRequesting,
    provider,
    wsStatus,
    retryCount,
    sendMessage,
    abort,
    reconnect,
  } = useGroupChat({
    group,
    historyMessages,
    activeSessionId: activeSession?.sessionId,
    refetchMessages: onReloadMessages,
  });
  const visibleMessages = useMemo(() => {
    return messages.filter((msg) => msg.content);
  }, [messages]);
  const hasPanelMessages = useMemo(() => {
    return visibleMessages.some(
      (msg) =>
        typeof msg.content === 'string' && hasAixPanelContent(msg.content),
    );
  }, [visibleMessages]);

  // 获取 Bot 列表
  const bots = useMemo(() => {
    return group?.participants?.filter((p) => p.type === 'bot') || [];
  }, [group]);

  // 从 store 获取群主 Bot 信息和 getBotName 方法
  const driverBot = useBotNetworkStore((state) => state.driverBot);

  // 获取 myBots 列表（包含 actor_kind 信息）
  const { myBots, selectDriverBot } = useBotNetwork();

  // 获取当前用户信息
  const userId = useUserStore((state) => state.userId);
  // human 昵称/头像由 useHumanIdentity（父级 GroupChat / SessionOnlyPage 调用）写入 userStore；
  // 此处直接读 store。身份来源差异（开源 /me、内部 __TERN__）已收口到 authAdapter。
  const userNickName = useUserStore((state) => state.nickName);
  const userAvatarUrl = useUserStore((state) => state.avatarUrl);
  const userDisplayName = userNickName || userId;

  // 融合模式（内部专属，代码不可见）：悬浮问答组件经 AppExt.slots.fuseChat 注入，开源默认 null（不渲染）。
  const { fuseChat: FuseSlot } = useExt(AppExt).slots;

  // 判断当前用户是否已在群聊中
  const isCurrentUserInGroup = useMemo(() => {
    if (!userId || !group?.participants) return false;
    const humanUuid = `human_${userId}`;
    const humanParticipant = group.participants.find(
      (p) => p.botUuid === humanUuid || p.id === humanUuid,
    );
    if (!humanParticipant) return false;
    return humanParticipant.mode === 'present';
  }, [userId, group]);

  // 是否为任务协作群（主从模式）
  const isManagerWorker = group.groupStrategy === 'manager_worker';

  // 会话模式下，判断用户是否在当前会话中
  const isCurrentUserInSession = useMemo(() => {
    if (!activeSession || !userId) return isCurrentUserInGroup;
    const humanActorId = `human_${userId}`;
    const member = activeSession.members.find(
      (m) => m.actorId === humanActorId,
    );
    return member?.mode === 'present';
  }, [activeSession, userId, isCurrentUserInGroup]);

  // 当前协作状态：会话模式用 session 级别，否则用 group 级别
  const isCurrentUserInCollab = activeSession
    ? isCurrentUserInSession
    : isCurrentUserInGroup;

  // 判断当前 driverBot 是否为 human 类型
  const isHumanDriverBot = useMemo(() => {
    if (!driverBot?.bot_uuid || !myBots?.length) return false;
    const currentBot = myBots.find((b) => b.bot_uuid === driverBot.bot_uuid);
    return currentBot?.actor_kind === 'human';
  }, [driverBot, myBots]);

  // 获取当前用户的在线状态（在线/隐身）
  const actorStatus = useMemo(() => {
    if (!isHumanDriverBot) return undefined;
    const humanBot = myBots?.find((b) => b.actor_kind === 'human');
    return humanBot?.status;
  }, [isHumanDriverBot, myBots]);

  // 切换到用户视角（选择 human 类型的 bot 作为 driverBot）
  const handleSwitchToHuman = useCallback(() => {
    if (!myBots?.length) {
      toast.error('未找到可用 Bot，请稍后重试');
      return;
    }
    const humanBot = myBots.find((b) => b.actor_kind === 'human');
    if (humanBot) {
      selectDriverBot({
        bot_uuid: humanBot.bot_uuid,
        bot_name: humanBot.bot_name,
        avatar_url: humanBot.avatar_url,
        visibility: humanBot.visibility,
        is_online: humanBot.is_online,
        status: humanBot.status,
      });
    } else {
      toast.error('未找到用户身份 Bot，请确认已加入协作网络');
    }
  }, [myBots, selectDriverBot]);

  // 获取 driverBot 在群聊中的 participant 信息
  // driverBot.bot_uuid 可能带 ":version" 后缀，participants 中通常只有基础 UUID
  const driverBotParticipant = useMemo(() => {
    if (!driverBot?.bot_uuid || !group?.participants) return null;
    const baseUuid = driverBot.bot_uuid.split(':')[0];
    return (
      group.participants.find(
        (p) =>
          p.botUuid === driverBot.bot_uuid ||
          p.id === driverBot.bot_uuid ||
          p.botUuid === baseUuid ||
          p.id === baseUuid,
      ) || null
    );
  }, [driverBot, group]);

  // Bot 自主发言的 mode（会话模式下从 session members 读取，否则从 group participants 读取）
  const currentBotMode: ParticipantMode = useMemo(() => {
    if (activeSession && driverBot?.bot_uuid) {
      const sessionMember = activeSession.members.find(
        (m) =>
          m.actorId === driverBot.bot_uuid ||
          m.actorId === driverBot.bot_uuid.split(':')[0],
      );
      return sessionMember?.mode || 'auto';
    }
    return driverBotParticipant?.mode || 'auto';
  }, [activeSession, driverBot?.bot_uuid, driverBotParticipant]);

  // 当前用户在群聊中的 participant 信息
  const humanParticipant = useMemo(() => {
    if (!userId || !group?.participants) return null;
    const humanUuid = `human_${userId}`;
    return (
      group.participants.find(
        (p) => p.botUuid === humanUuid || p.id === humanUuid,
      ) || null
    );
  }, [userId, group]);

  const currentHumanMode: ParticipantMode | null =
    humanParticipant?.mode ?? null;

  // 使用 useActor hook
  const { updateParticipantMode } = useActor();

  const [isUpdatingMode, setIsUpdatingMode] = useState(false);
  const [isPanelManifestReady, setIsPanelManifestReady] = useState(false);

  // 副屏 ref — ChatLayout.Panel 暴露 PanelHandle
  const panelRef = useRef<PanelHandle>(null);

  // Sender ref — 用于 fill_input 功能
  // const senderRef = useRef<{
  //   insert: (text: string) => void;
  //   focus: () => void;
  // } | null>(null);

  // 预加载协作页需要的 CDN 配置。AixPanel 挂载时只读取一次全局 CDN map，
  // 所以历史消息列表会等 manifest 完成后再挂载。
  useEffect(() => {
    let cancelled = false;

    setIsPanelManifestReady(false);
    queryAndRegisterManifestCdnPanels()
      .then((count) => {
        if (count > 0) {
          console.log(
            `[GroupChat] BCS manifest CDN 预加载完成: ${count} 个组件`,
          );
        }
      })
      .finally(() => {
        if (!cancelled) {
          setIsPanelManifestReady(true);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [group.id]);

  // 加入协作处理
  const handleJoinCollaboration = useCallback(async () => {
    if (!userId) return;
    const humanActorId = `human_${userId}`;

    if (activeSession && onJoinSession) {
      // 会话级加入
      const success = await onJoinSession(
        activeSession.sessionId,
        humanActorId,
      );
      if (success) {
        onRefreshSession?.(activeSession.sessionId);
      }
    } else {
      // 群级加入
      if (!group?.id) return;
      try {
        const success = await updateParticipantMode(
          group.id,
          humanActorId,
          'present',
        );
        if (success) {
          onRefreshGroup?.();
        }
      } catch (error) {
        console.error('[GroupChatPage] Failed to join collaboration:', error);
      }
    }
  }, [
    activeSession,
    onJoinSession,
    group?.id,
    userId,
    updateParticipantMode,
    onRefreshGroup,
    onRefreshSession,
  ]);

  // 退出协作处理（支持会话级和群级）
  const handleLeaveCollaboration = useCallback(async () => {
    if (!userId) return;
    const humanActorId = `human_${userId}`;

    if (activeSession && onLeaveSession) {
      // 会话级退出
      const success = await onLeaveSession(
        activeSession.sessionId,
        humanActorId,
      );
      if (success) {
        onRefreshSession?.(activeSession.sessionId);
      }
    } else {
      // 群级退出
      if (!group?.id) return;
      try {
        const success = await updateParticipantMode(
          group.id,
          humanActorId,
          'absent',
        );
        if (success) {
          onRefreshGroup?.();
        }
      } catch (error) {
        console.error('[GroupChatPage] Failed to leave collaboration:', error);
      }
    }
  }, [
    activeSession,
    onLeaveSession,
    group?.id,
    userId,
    updateParticipantMode,
    onRefreshGroup,
    onRefreshSession,
  ]);

  // 处理 mode 切换（会话模式下走 session 级接口，否则走 group 级接口）
  const handleModeChange = useCallback(
    async (newMode: ParticipantMode) => {
      const actorId =
        driverBotParticipant?.botUuid ||
        driverBotParticipant?.id ||
        driverBot?.bot_uuid;
      if (!actorId) return;

      setIsUpdatingMode(true);
      try {
        if (activeSession && onUpdateSessionMemberMode) {
          // 会话模式：使用 session 级别的成员模式接口，刷新 session 详情
          const success = await onUpdateSessionMemberMode(
            activeSession.sessionId,
            actorId,
            newMode as 'auto' | 'muted',
          );
          if (success) {
            onRefreshSession?.(activeSession.sessionId);
          }
        } else {
          // 群组模式：使用 group 级别的参与者模式接口
          if (!group?.id) return;
          const success = await updateParticipantMode(
            group.id,
            actorId,
            newMode as 'present' | 'absent' | 'auto' | 'muted',
          );
          if (success) {
            onRefreshGroup?.();
          }
        }
      } finally {
        setIsUpdatingMode(false);
      }
    },
    [
      activeSession,
      group?.id,
      driverBotParticipant,
      updateParticipantMode,
      onUpdateSessionMemberMode,
      onRefreshGroup,
      onRefreshSession,
    ],
  );

  // Mode 配置
  const modeConfig = useMemo(() => {
    const configs: Record<
      ParticipantMode | 'assistant',
      {
        label: string;
        description: string;
        icon: any;
        color: string;
        disabled?: boolean;
        badge?: string;
      }
    > = {
      auto: {
        label: '自动模式',
        description:
          'bot会根据群内对话语境，@提及等情况自主决策是否回复，适合日常协作场景。',
        icon: Cpu,
        color: 'bg-blue-500',
      },
      assistant: {
        label: '辅助模式',
        description:
          'Bot回复内容生成后，需经用户审核后再发出，适合特殊议题或正式场合必须先审后发场景。',
        icon: ShieldCheck,
        color: 'bg-slate-300',
        disabled: true,
        badge: '开发中',
      },
      muted: {
        label: '禁言模式',
        description:
          'bot将完全保持沉默，不会回复任何消息。适合需要人工接管或临时静默的场景。',
        icon: VolumeX,
        color: 'bg-slate-400',
      },
      present: {
        label: '参与模式',
        description: '以用户身份参与群聊互动',
        icon: Bot,
        color: 'bg-green-500',
      },
      absent: {
        label: '旁观模式',
        description: '仅接收消息，不参与互动',
        icon: Bot,
        color: 'bg-slate-400',
      },
    };
    return configs;
  }, []);

  const currentModeConfig = modeConfig[currentBotMode] || modeConfig.auto;

  // 渲染群聊消息的头像
  const getSenderInfo = useCallback(
    (msg: ChatMessage) => {
      // extra 字段统一来源:
      // - 历史:transformGroupMessagesToChatMessages 写 camelCase
      // - 实时:SDK GroupChatProvider.resultToMessage 已在 SDK 层归一成 camelCase
      // 这里直接解构,不再做 fallback 兜底
      // @ts-ignore - extra 是自定义字段
      const extra: any = msg.extra || {};
      const { botUuid, botName, senderName, senderAvatar } = extra;
      const isUser = msg.role === 'user';

      if (isUser) {
        const humanUuid = `human_${userId}`;
        // driverBot.bot_uuid 可能带 ":version" 后缀,WS 消息里通常只有基础 UUID,
        // 这里两边都按 ":" 取基础段比较,避免 bot 视角下自己的消息被判成"其他用户"
        const driverBotBaseUuid = driverBot?.bot_uuid?.split(':')[0];
        const msgBotBaseUuid = botUuid?.split(':')[0];
        const isHumanMe = botUuid === humanUuid;
        const isDriverBotMe =
          !!driverBotBaseUuid &&
          !!msgBotBaseUuid &&
          msgBotBaseUuid === driverBotBaseUuid;
        const isCurrentUser = isHumanMe || isDriverBotMe;

        if (isCurrentUser) {
          // 当前用户的消息 → 右对齐
          // 来源是 human 时显示用户信息;来源是 driverBot(bot 视角)时显示 bot 信息
          const humanParticipant = group?.participants?.find(
            (p) => p.botUuid === humanUuid || p.id === humanUuid,
          );
          const displayName = isHumanMe
            ? humanParticipant?.name || userDisplayName || driverBot?.bot_name
            : driverBot?.bot_name || humanParticipant?.name || userDisplayName;
          const avatarUrl = isHumanMe
            ? userAvatarUrl || driverBot?.avatar_url
            : driverBot?.avatar_url || userAvatarUrl;
          return {
            role: 'user' as const,
            avatar: (
              <MessageAvatar
                type="bot-letter"
                name={displayName}
                botId={driverBot?.bot_uuid?.split(':')[0]}
                avatarUrl={avatarUrl}
              />
            ),
            name: displayName,
          };
        } else {
          // 其他用户的消息 → 左对齐
          const participantInfo = group?.participants?.find(
            (p) => p.botUuid === botUuid || p.id === botUuid,
          );
          const displayName =
            participantInfo?.name || senderName || botUuid || '';
          return {
            role: 'other-user' as const,
            avatar: (
              <MessageAvatar
                type="user"
                name={displayName}
                botId={botUuid?.split(':')[0]}
                avatarUrl={senderAvatar}
              />
            ),
            name: displayName,
            bubbleColor: 'var(--aix-color-bg-subtle, #f7faff)',
            maxWidth: 'fit-content',
          };
        }
      } else {
        const participantInfo = group?.participants?.find(
          (p) => p.botUuid === botUuid,
        );
        const displayName = participantInfo?.name || botName || '';
        const isHuman = participantInfo?.actorKind === 'human';
        return {
          role: 'assistant' as const,
          avatar: (
            <MessageAvatar
              type={isHuman ? 'user' : 'assistant'}
              name={displayName}
              botId={botUuid?.split(':')[0]}
              avatarUrl={senderAvatar}
            />
          ),
          name: displayName,
          bubbleColor: 'var(--aix-color-bg-subtle, #f7faff)',
          maxWidth: 'fit-content',
        };
      }
    },
    [group, driverBot, userAvatarUrl, userId, userDisplayName],
  );

  // 构建 mention 配置
  const mentionCategories = useMemo(() => {
    const allItem: MentionItem = {
      id: 'ALL',
      name: 'ALL',
      description: '提及所有人',
      disabled: isManagerWorker,
      avatar: (
        <div className="w-5 h-5 rounded-full bg-lavender-100 flex items-center justify-center flex-shrink-0">
          <Users className="w-3 h-3 text-lavender-600" />
        </div>
      ),
    };

    // 会话级：以 activeSession.members 为主，从 group.participants 补全 name/avatar
    // 群级：以 bots（group.participants 中的 bot）为主
    const botItems: MentionItem[] = activeSession
      ? activeSession.members
          .filter((sm) => sm.actorKind !== 'human' && sm.mode !== 'absent')
          .map((sm) => {
            const matched = bots.find(
              (p) => p.botUuid === sm.actorId || p.id === sm.actorId,
            );
            const displayName =
              sm.actorId === driverBot?.bot_uuid
                ? driverBot?.bot_name
                : sm.name || matched?.name || sm.actorId;
            const isManager =
              isManagerWorker &&
              (sm.role === 'manager' || sm.actorId === group.masterBot);
            return {
              id: sm.actorId,
              name: displayName,
              description: isManagerWorker
                ? sm.role === 'manager'
                  ? '管理员'
                  : '成员'
                : sm.role === 'driver'
                ? '群主'
                : '成员',
              disabled: isManagerWorker && !isManager,
              avatar: (
                <MessageAvatar
                  type="assistant"
                  botId={sm.actorId?.split(':')[0]}
                  name={displayName}
                  avatarUrl={
                    sm.actorId === driverBot?.bot_uuid
                      ? driverBot?.avatar_url
                      : matched?.avatar
                  }
                />
              ),
            };
          })
      : bots.map((bot) => {
          const displayName =
            bot.botUuid === driverBot?.bot_uuid
              ? driverBot?.bot_name
              : bot.name;
          const isManager =
            isManagerWorker &&
            (bot.role === 'manager' || bot.botUuid === group.masterBot);
          return {
            id: bot.botUuid || bot.id,
            name: displayName,
            description: isManagerWorker
              ? bot.role === 'manager'
                ? '管理员'
                : '成员'
              : bot.role === 'driver'
              ? '群主'
              : '成员',
            disabled: isManagerWorker && !isManager,
            avatar: (
              <MessageAvatar
                type="assistant"
                botId={bot.botUuid?.split(':')[0]}
                name={displayName}
                avatarUrl={
                  bot.botUuid === driverBot?.bot_uuid
                    ? driverBot?.avatar_url
                    : bot.avatar
                }
              />
            ),
          };
        });

    // 任务协作模式下，自定义渲染禁用项（置灰 + Tooltip 提示）
    const renderMentionItem = isManagerWorker
      ? (item: MentionItem) => {
          if (!item.disabled) {
            return (
              <>
                <div className="w-7 h-7 rounded-full overflow-hidden flex-shrink-0">
                  {item.avatar}
                </div>
                <div className="flex flex-col gap-px min-w-0">
                  <span className="text-sm font-medium text-slate-800 truncate">
                    {item.name}
                  </span>
                  {item.description && (
                    <span className="text-[11px] text-slate-400 truncate">
                      {item.description}
                    </span>
                  )}
                </div>
              </>
            );
          }

          const tooltipText = '主从模式任务协作群中，用户仅可与主节点Bot对话';

          return (
            <Tooltip>
              <TooltipTrigger asChild>
                <div className="flex items-center gap-2.5 w-full opacity-40 cursor-not-allowed">
                  <div className="w-7 h-7 rounded-full overflow-hidden flex-shrink-0 grayscale">
                    {item.avatar}
                  </div>
                  <div className="flex flex-col gap-px min-w-0">
                    <span className="text-sm font-medium text-slate-400 truncate">
                      {item.name}
                    </span>
                    {item.description && (
                      <span className="text-[11px] text-slate-300 truncate">
                        {item.description}
                      </span>
                    )}
                  </div>
                </div>
              </TooltipTrigger>
              <TooltipContent side="top">{tooltipText}</TooltipContent>
            </Tooltip>
          );
        }
      : undefined;

    return [
      {
        key: 'bots',
        label: 'Bot 成员',
        items: [allItem, ...botItems],
        ...(renderMentionItem && { renderItem: renderMentionItem }),
      },
    ];
  }, [bots, driverBot, isManagerWorker, group.masterBot, activeSession]);

  // 包装 sendMessage 以支持 sessionId，并校验用户是否已加入会话
  const handleSendMessage = useCallback(
    (msg: string, mentions?: string[], senderId?: string) => {
      if (!isCurrentUserInCollab) {
        toast.warning(
          activeSession
            ? '请先加入当前会话后再发送消息'
            : '请先加入协作后再发送消息',
        );
        return;
      }
      sendMessage(msg, mentions, senderId, activeSession?.sessionId);
    },
    [
      sendMessage,
      activeSession?.sessionId,
      isCurrentUserInCollab,
      activeSession,
    ],
  );

  if (!group) {
    return null;
  }

  return (
    <TooltipProvider>
      <div className="h-full flex flex-col min-w-0 bg-white overflow-hidden min-h-0">
        <ChatLayout>
          {/* 头部 */}
          {isMobile && activeSession ? (
            /* 移动端轻量版：返回 + 会话标题 */
            <div className="flex items-center gap-2 px-3 py-2.5 border-b border-slate-100 bg-white flex-shrink-0">
              <button
                type="button"
                onClick={() => onBackToSessionList?.()}
                className="p-1 rounded-md active:bg-slate-100 transition-colors flex-shrink-0"
              >
                <ArrowLeft className="w-4 h-4 text-slate-500" />
              </button>
              <span className="text-sm font-medium text-slate-800 truncate">
                {activeSession.sessionTitle || '新会话'}
              </span>
            </div>
          ) : !isMobile ? (
            activeSession ? (
              <SessionHeader
                sessionId={activeSession.sessionId}
                sessionTitle={activeSession.sessionTitle}
                groupName={group.topic}
                groupGoal={group.extra?.goal || undefined}
                groupMemberCount={group.participants.length}
                sessionMemberCount={activeSession.members.length}
                onTitleUpdate={(title) =>
                  onUpdateSessionTitle?.(activeSession.sessionId, title)
                }
                onBack={() => onBackToSessionList?.()}
                isUpdatingTitle={isUpdatingSessionTitle}
              />
            ) : (
              <div className="flex items-center justify-between px-5 py-3 border-b border-slate-200 bg-white">
                <div className="flex items-center gap-3">
                  <div>
                    <div className="flex items-center gap-2">
                      <h2 className="font-medium text-slate-800 text-sm">
                        {group.topic}
                      </h2>
                    </div>
                    <p className="text-[10px] text-slate-400">
                      {group.participants.length} 位成员
                    </p>
                  </div>
                </div>
              </div>
            )
          ) : null}

          {/* 消息列表 */}
          {(hasPanelMessages && !isPanelManifestReady) ||
          (isLoadingMessages && visibleMessages.length === 0) ? (
            <Skeleton.Message className="flex-1" />
          ) : (
            <MessageList
              key={group.id}
              messages={visibleMessages}
              isTyping={isRequesting}
              isLoadingMore={isLoadingMessages}
              getSenderConfig={(msg) => getSenderInfo(msg)}
              messageGap="pb-6"
              emptyPlaceholder={
                <Empty
                  size="lg"
                  icon={<Bot />}
                  title="开始群聊对话吧"
                  description="使用 @ 提及 Bot"
                  className="h-full"
                />
              }
              className="px-5 py-4"
            />
          )}

          {/* 底部面板 */}
          <BottomPanel
            group={group}
            isMobile={isMobile}
            isCurrentUserInGroup={isCurrentUserInCollab}
            isHumanDriverBot={isHumanDriverBot}
            currentBotMode={currentBotMode}
            currentModeConfig={currentModeConfig}
            currentHumanMode={currentHumanMode}
            modeConfig={modeConfig}
            isUpdatingMode={isUpdatingMode}
            userDisplayName={userDisplayName}
            userAvatarUrl={userAvatarUrl}
            driverBot={driverBot}
            bots={bots}
            provider={provider}
            isRequesting={isRequesting}
            mentionCategories={mentionCategories}
            userId={userId}
            wsStatus={wsStatus}
            retryCount={retryCount}
            onReconnect={reconnect}
            needsBcnInit={needsBcnInit}
            isJoiningBcn={isJoiningBcn}
            isManagerWorker={isManagerWorker}
            activeSessionId={activeSession?.sessionId || null}
            onModeChange={handleModeChange}
            onJoinCollaboration={handleJoinCollaboration}
            onLeaveCollaboration={handleLeaveCollaboration}
            onSendMessage={handleSendMessage}
            onAbort={abort}
            onJoinBcn={onJoinBcn}
            onSwitchToHuman={handleSwitchToHuman}
            actorStatus={actorStatus}
          />

          {/* 副屏：业务卡片可通过 chatBridge.openPanelTab(...) 唤起 */}
          <ChatLayout.Panel
            ref={panelRef}
            bridge={chatBridge}
            onAction={(action: any) => {
              if (action.type === 'send_message') {
                sendMessage(action.content, undefined, driverBot?.bot_uuid);
              } else if (action.type === 'fill_input') {
                // 填充输入框内容（从 bridge 获取 ref）
                const inputRef = chatBridge.getInputRef();
                inputRef?.insert(action.content);
                inputRef?.focus();
              }
            }}
          />
        </ChatLayout>

        {/* 智能问答悬浮按钮和聊天窗口（融合模式）：开源裁掉，内部通过 slot 注入 */}
        {FuseSlot && <FuseSlot group={group} />}
      </div>
    </TooltipProvider>
  );
};

export default GroupChatPage;
