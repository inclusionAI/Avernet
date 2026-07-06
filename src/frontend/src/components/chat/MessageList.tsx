import type { SenderConfig } from '@/components/chat/types';
import { formatTime } from '@/components/chat/utils/formatTime';
import { getMessageBlocks } from '@/components/chat/utils/getMessageBlocks';
import type { MessageAvatarType } from '@/components/MessageAvatar';
import MessageAvatar from '@/components/MessageAvatar';
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip';
import { cn } from '@/utils/utils';
import type { ChatMessage } from '@aix-chat/core';
import {
  Actions,
  aixUiPlugin,
  Bubble,
  ChatList,
  SelectionToolbar,
  SystemNotice,
} from '@aix-chat/ui';
import { Check, Copy } from 'lucide-react';
import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';

/**
 * 长任务等待分级提示
 *
 * - <60s：无提示（默认气泡的 streaming 动画已经能传达状态）
 * - 60s–150s：柔性提示（Agent 长思考/工具调用场景）
 * - >=150s：显著提示（超时即将触发——基类 heartbeatTimeout 默认 5 分钟）
 */
function useLongWaitHint(active: boolean): string {
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    if (!active) {
      setElapsed(0);
      return;
    }
    const start = Date.now();
    // 每 5s 更新一次即可，避免每秒 re-render 拖累消息列表
    const t = setInterval(() => {
      setElapsed(Math.floor((Date.now() - start) / 1000));
    }, 5000);
    return () => clearInterval(t);
  }, [active]);

  if (!active) return '';
  if (elapsed < 60) return '';
  if (elapsed < 150) return `正在思考,已等待 ${elapsed}s…`;
  return `长任务可能需要更长时间,已等待 ${elapsed}s,请耐心等待…`;
}

/**
 * 系统消息项
 *
 * SDK 的 `SystemNotice` 在 `maxLines` 截断时会自动填充浏览器原生 `title` 属性,
 * 触发的是无样式控制、显示延迟较长的系统 tooltip。这里:
 *   - 传 `tooltip=""` 关掉原生 title
 *   - 仅在内容真正被截断时(ResizeObserver 监听)挂载 shadcn Tooltip
 *   - 短消息不重复弹气泡,避免悬停噪声
 */
const SystemMessageItem: React.FC<{ msg: ChatMessage }> = ({ msg }) => {
  const wrapperRef = useRef<HTMLDivElement>(null);
  const [isOverflowing, setIsOverflowing] = useState(false);

  useEffect(() => {
    const root = wrapperRef.current;
    if (!root) return;
    const check = () => {
      // SystemNotice 内部 `Content` 节点带 overflow:hidden + white-space:nowrap,
      // 截断时 scrollWidth 会大于 clientWidth,遍历找到即可。
      let overflowing = false;
      root.querySelectorAll<HTMLElement>('div').forEach((d) => {
        if (d.scrollWidth > d.clientWidth + 1) overflowing = true;
      });
      setIsOverflowing(overflowing);
    };
    check();
    const ro = new ResizeObserver(check);
    ro.observe(root);
    return () => ro.disconnect();
  }, [msg.content]);

  return (
    <div
      data-message-id={msg.id}
      className="flex items-center justify-center gap-1 pb-3 min-w-0 max-w-full"
    >
      <span className="text-[11px] text-slate-400 shrink-0">
        {formatTime(msg.timestamp)}
      </span>
      <TooltipProvider delayDuration={250}>
        <Tooltip>
          <TooltipTrigger asChild>
            <div
              ref={wrapperRef}
              className={cn(
                'min-w-0 max-w-[60%]',
                isOverflowing && 'cursor-help',
              )}
            >
              <SystemNotice
                variant="inline"
                severity="neutral"
                maxLines={1}
                tooltip=""
                className="min-w-0 max-w-full"
              >
                {msg.content}
              </SystemNotice>
            </div>
          </TooltipTrigger>
          {isOverflowing && (
            <TooltipContent
              side="top"
              align="center"
              sideOffset={6}
              className="max-w-[420px] whitespace-pre-wrap break-words text-left leading-5 text-[12px] bg-slate-800/95 text-slate-50 shadow-lg"
            >
              {msg.content}
            </TooltipContent>
          )}
        </Tooltip>
      </TooltipProvider>
    </div>
  );
};

export interface MessageListProps {
  /** 消息列表 */
  messages: ChatMessage[];
  /** AI 正在响应（展示 typing / thinking 气泡） */
  isTyping?: boolean;
  /** 列表顶部是否还有更多历史可加载 */
  hasMore?: boolean;
  /** 是否正在加载更多 */
  isLoadingMore?: boolean;
  /** 滚到顶部触发加载更多 */
  onLoadMore?: () => void;
  /** 审批批准回调 */
  onApprove?: (approvalId: string) => void;
  /** 审批拒绝回调 */
  onReject?: (approvalId: string) => void;
  /** 划词追问回调 */
  onFollowUp?: (text: string) => void;
  /** 划词解释回调 */
  onExplain?: (text: string) => void;
  /** 划词工具栏 ID */
  selectionToolbarId?: string;
  /** 过滤消息的 sessionKey */
  sessionKey?: string;
  /** 空状态提示 */
  emptyPlaceholder?: string | React.ReactNode;
  /** 历史加载中文案 */
  historyLoadingPlaceholder?: string;
  /** assistant 消息头像类型（默认 'assistant'） */
  assistantAvatarType?: Extract<
    MessageAvatarType,
    'assistant' | 'assistant-expert'
  >;
  /** Bot 头像 URL（或 "emoji:🤖" 格式），优先显示 */
  botAvatarUrl?: string | null;
  /** Bot 名称，用于生成首字母头像 */
  botName?: string;
  /** Bot ID，用于哈希决定头像颜色 */
  botId?: string;
  /** 反转头像分配（他人会话） */
  reverseAvatar?: boolean;
  /** 创建者用户 ID（reverseAvatar=true 时用于显示 assistant 名称） */
  creatorUserId?: string | null;
  /** 自定义头像渲染（优先级高于默认头像） */
  renderAvatar?: (msg: ChatMessage) => React.ReactNode;
  /** 自定义 Sender 配置（优先级最高） */
  getSenderConfig?: (msg: ChatMessage) => Partial<SenderConfig>;
  /** 容器类名 */
  className?: string;
  /** 消息之间的间距类名（默认 'pb-4'） */
  messageGap?: string;
}

/**
 * 消息列表组件（OpenClaw 引擎）
 *
 * 职责：
 * - 把业务消息（ChatMessage）翻译成 SDK ChatList 需要的 renderItem
 * - 处理业务 props：头像、角色反转、发送者名称等
 * - 挂载划词工具栏（SelectionToolbar）
 * - thinking bubble 作为 footer 传给 ChatList
 *
 * 滚动管理（自动跟随）、加载更多触发由 SDK ChatList 负责。
 *
 */

function CopyButton({ content }: { content: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <Actions
      items={[
        {
          key: 'copy',
          icon: copied ? (
            <Check size={14} className="text-green-500" />
          ) : (
            <Copy size={14} />
          ),
          label: copied ? '已复制' : '复制',
          onClick: () => {
            navigator.clipboard.writeText(content);
            setCopied(true);
            setTimeout(() => setCopied(false), 1500);
          },
        },
      ]}
    />
  );
}

const MessageListInner: React.FC<MessageListProps> = ({
  messages,
  isTyping,
  hasMore,
  isLoadingMore,
  onLoadMore,
  onApprove,
  onReject,
  onFollowUp,
  onExplain,
  selectionToolbarId = 'chat-selection',
  sessionKey,
  emptyPlaceholder = '开始对话吧...',
  historyLoadingPlaceholder = '消息加载中...',
  assistantAvatarType = 'assistant',
  botAvatarUrl,
  botName,
  botId,
  reverseAvatar = false,
  creatorUserId,
  renderAvatar,
  getSenderConfig,
  className,
  messageGap = 'pb-4',
}) => {
  const selectionContainerRef = useRef<HTMLDivElement>(null);

  // 初始滚底标记：会话切换后首次加载消息时，需要确保滚动到底部
  const hasInitialScrolledRef = useRef(false);

  // 自动跟随：追踪用户是否主动上滑，用于补充 SDK 的 followOutput 机制。
  // SDK 的 atBottomRef 用 scrollHeight - scrollTop - clientHeight 判断是否在底部，
  // 但流式输出时内容增长导致 scrollHeight 变大、scrollTop 不变，distanceFromBottom 增大，
  // atBottomRef 被误判为 false，自动滚动就停了。
  // 改用滚动方向检测：只有 scrollTop 减小（用户主动上滑）才停止跟随，
  // 内容增长导致的 distanceFromBottom 变化不影响跟随状态。
  const autoFollowRef = useRef(true);
  const lastScrollTopRef = useRef(0);
  const prevMessageCountRef = useRef(0);

  // 过滤消息
  const filteredMessages = useMemo(() => {
    return sessionKey
      ? messages.filter(
          (msg) => !msg.sessionKey || msg.sessionKey === sessionKey,
        )
      : messages;
  }, [messages, sessionKey]);

  // 头像类型推断
  const getAvatarType = useCallback(
    (role: string): MessageAvatarType => {
      const isUser = role === 'user';
      if (reverseAvatar) return isUser ? assistantAvatarType : 'user';
      return isUser ? 'user' : assistantAvatarType;
    },
    [reverseAvatar, assistantAvatarType],
  );

  // 计算单条消息的 senderConfig
  const buildSenderConfig = useCallback(
    (msg: ChatMessage): SenderConfig => {
      const isAssistantRole = reverseAvatar
        ? msg.role === 'user'
        : msg.role === 'assistant';
      const displayName = reverseAvatar
        ? creatorUserId
        : isAssistantRole
        ? botName
        : undefined;

      let senderConfig: SenderConfig = {
        role: msg.role,
        avatar: (
          <MessageAvatar
            type={getAvatarType(msg.role)}
            avatarUrl={isAssistantRole ? botAvatarUrl : undefined}
            name={displayName}
            botId={botId}
          />
        ),
        name: displayName,
        bubbleColor: msg.role === 'user' ? '#dbeafe' : undefined,
      };
      if (getSenderConfig) {
        senderConfig = { ...senderConfig, ...getSenderConfig(msg) };
      }
      if (renderAvatar) {
        senderConfig.avatar = renderAvatar(msg);
      }
      return senderConfig;
    },
    [
      reverseAvatar,
      creatorUserId,
      botName,
      botAvatarUrl,
      botId,
      getAvatarType,
      getSenderConfig,
      renderAvatar,
    ],
  );

  // 初始滚底：会话切换后首次加载消息时，延迟滚动到底部
  useEffect(() => {
    if (filteredMessages.length === 0) {
      hasInitialScrolledRef.current = false;
      return;
    }
    if (hasInitialScrolledRef.current) return;
    hasInitialScrolledRef.current = true;

    // 等待渲染完成后再滚动
    const timer = setTimeout(() => {
      const wrapper = selectionContainerRef.current;
      if (!wrapper) return;
      // BubbleList DOM 结构: wrapper > 外层div > 滚动容器div(overflow:auto)
      const scrollEl = wrapper.firstElementChild
        ?.firstElementChild as HTMLElement | null;
      if (scrollEl) {
        scrollEl.scrollTop = scrollEl.scrollHeight;
      }
    }, 200);
    return () => clearTimeout(timer);
  }, [filteredMessages.length]);

  // 自动跟随 — scroll 监听：通过滚动方向判断用户是否主动上滑
  // 核心思路：scrollTop 减小 = 用户主动上滑 → 停止跟随
  //          滚回底部附近 → 恢复跟随
  //          内容增长导致 scrollHeight 变大但 scrollTop 不变 → 不影响跟随状态
  useEffect(() => {
    const wrapper = selectionContainerRef.current;
    if (!wrapper) return;
    // BubbleList DOM 结构: wrapper > 外层div > 滚动容器div(overflow:auto)
    const scrollEl = wrapper.firstElementChild
      ?.firstElementChild as HTMLElement | null;
    if (!scrollEl) return;

    const handleScroll = () => {
      const currentScrollTop = scrollEl.scrollTop;
      // scrollTop 明显减小 → 用户主动上滑
      if (currentScrollTop < lastScrollTopRef.current - 20) {
        autoFollowRef.current = false;
      }
      // 滚回底部附近 → 恢复跟随
      const distanceFromBottom =
        scrollEl.scrollHeight - currentScrollTop - scrollEl.clientHeight;
      if (distanceFromBottom < 50) {
        autoFollowRef.current = true;
      }
      lastScrollTopRef.current = currentScrollTop;
    };

    scrollEl.addEventListener('scroll', handleScroll, { passive: true });
    return () => scrollEl.removeEventListener('scroll', handleScroll);
  }, []);

  // 自动跟随 — 内容变化时滚底
  // 新消息追加时（消息数增加），强制重置 autoFollow 并滚到底部，
  // 确保发送消息后即使之前在上滑查看历史，也能回到底部跟随新内容。
  // 流式输出时消息对象更新但数量不变，由 autoFollowRef 状态决定是否跟随。
  useEffect(() => {
    if (filteredMessages.length === 0) {
      prevMessageCountRef.current = 0;
      return;
    }

    const isNewMessage = filteredMessages.length > prevMessageCountRef.current;
    prevMessageCountRef.current = filteredMessages.length;

    // 新消息追加 → 强制回到底部
    if (isNewMessage) {
      autoFollowRef.current = true;
    }

    if (!autoFollowRef.current) return;

    const wrapper = selectionContainerRef.current;
    if (!wrapper) return;
    const scrollEl = wrapper.firstElementChild
      ?.firstElementChild as HTMLElement | null;
    if (!scrollEl) return;

    const raf = requestAnimationFrame(() => {
      if (!autoFollowRef.current) return;
      scrollEl.scrollTop = scrollEl.scrollHeight;
    });
    return () => cancelAnimationFrame(raf);
  }, [filteredMessages]);

  // 最后一条消息（用于判断是否在流式输出中）
  const lastMsg = filteredMessages[filteredMessages.length - 1];
  const showThinkingBubble = isTyping && lastMsg?.role === 'user';

  // renderItem — OpenClaw 简单 Bubble 渲染
  const renderItem = useCallback(
    (msg: ChatMessage, index: number) => {
      // 系统消息: 居中 inline 提示, 早 return 跳过 buildSenderConfig / getSenderConfig,
      // 避免任何按 botUuid 找头像的逻辑被触发
      if (msg.role === 'system') {
        return <SystemMessageItem msg={msg} />;
      }

      const isLastMsg = index === filteredMessages.length - 1;
      const senderConfig = buildSenderConfig(msg);

      return (
        <div data-message-id={msg.id} className={cn('min-w-0', messageGap)}>
          <Bubble
            sender={senderConfig}
            timestamp={formatTime(msg.timestamp)}
            blocks={getMessageBlocks(msg)}
            markdown={{ preset: 'full', extensions: [aixUiPlugin] }}
            className={senderConfig.role === 'user' ? 'ml-auto' : 'mr-auto'}
            isStreaming={isLastMsg && isTyping && !showThinkingBubble}
            tool={{ defaultCollapsed: !(isLastMsg && isTyping) }}
            approval={
              onApprove || onReject ? { onApprove, onReject } : undefined
            }
            actions={
              msg.role === 'assistant' ? (
                <CopyButton content={msg.content || ''} />
              ) : undefined
            }
          />
        </div>
      );
    },
    [
      filteredMessages,
      buildSenderConfig,
      isTyping,
      showThinkingBubble,
      onApprove,
      onReject,
    ],
  );

  // Thinking bubble 作为 footer — OpenClaw 用 Bubble
  const waitHint = useLongWaitHint(!!showThinkingBubble);
  const footer = showThinkingBubble ? (
    <div className={cn('min-w-0', messageGap)}>
      <Bubble
        sender={{
          role: 'assistant',
          avatar: (
            <MessageAvatar
              type={assistantAvatarType}
              avatarUrl={botAvatarUrl}
              name={botName}
              botId={botId}
            />
          ),
        }}
        blocks={[{ type: 'text', content: '' }]}
        markdown={{ preset: 'full', extensions: [aixUiPlugin] }}
        className="mr-auto"
        isStreaming={true}
      />
      {waitHint && (
        <div className="mt-1 pl-12 text-xs text-slate-400">{waitHint}</div>
      )}
    </div>
  ) : null;

  const emptyNode =
    isLoadingMore && filteredMessages.length === 0 ? (
      <div className="text-gray-400 text-sm">{historyLoadingPlaceholder}</div>
    ) : (
      <div className="text-gray-400 text-sm">{emptyPlaceholder}</div>
    );

  return (
    <div
      ref={selectionContainerRef}
      className={cn('flex-1 flex flex-col overflow-hidden min-w-0', className)}
    >
      <ChatList<ChatMessage>
        className="flex-1 pl-6 pr-0 openclaw-message-list"
        messages={filteredMessages}
        renderItem={renderItem}
        computeItemKey={(msg) => msg.id}
        hasMore={hasMore}
        isLoadingMore={isLoadingMore}
        onLoadMore={onLoadMore}
        footer={footer}
        emptyPlaceholder={emptyNode}
        // 禁用 SDK 内置的 followOutput 和 isStreaming 滚动机制：
        // SDK 的 atBottomRef 在流式输出时因 scrollHeight 变化而误判为"不在底部"，
        // 导致自动滚动停止。由 MessageList 的 autoFollow 机制替代。
        followOutput={false}
      />

      {/* 划词操作栏 — 留在 MessageList 薄壳层，SDK 不内置 */}
      {(onFollowUp || onExplain) && (
        <div className="hidden md:block">
          <SelectionToolbar
            messageId={selectionToolbarId}
            containerRef={selectionContainerRef}
            onFollowUp={(_id, text) => onFollowUp?.(text)}
            onExplain={(_id, text) => onExplain?.(text)}
          />
        </div>
      )}
    </div>
  );
};

export const MessageList = React.memo(MessageListInner);

export default MessageList;
