import { isSameHumanIdentity } from '@/domain/userIdentity';
import { buildGroupMentionConfig, expandMentionIds } from '@/pages/Workspace/components/GroupChatPane/mentionHelpers';
import { createBotChatProvider } from '@/services/workspace/botChatProvider';
import { botSessionService, type ChatBotView } from '@/services/workspace/botSessionService';
import { createGroupChatProvider } from '@/services/workspace/groupChatProvider';
import { resolveGroupWsOrigin } from '@/services/workspace/groupChatProviderHelpers';
import { sessionService } from '@/services/workspace/sessionService';
import type { TaskLinkedSessionTarget } from '@/stores/taskSidePanelStore';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import type { MentionConfig, SubmitContext } from '@tc-chat/ui';
import { Sender } from '@tc-chat/ui/es/Sender';
import { useCallback, useEffect, useRef, useState } from 'react';
import { toast } from 'sonner';

type LinkedSessionProvider =
  | { kind: 'bot'; provider: ReturnType<typeof createBotChatProvider> }
  | { kind: 'group'; provider: ReturnType<typeof createGroupChatProvider> };

const BOT_CONVERSATION_REFRESH_DELAYS_MS = [400, 1600] as const;

type ComposerPhase = 'loading' | 'ready' | 'disabled' | 'error';

interface ComposerState {
  scopeKey: string;
  phase: ComposerPhase;
  message: string;
}

function findOwnedBot(bots: ChatBotView[], targetBotId: string): ChatBotView | null {
  const separator = targetBotId.indexOf(':');
  const realBotId = separator >= 0 ? targetBotId.slice(0, separator) : targetBotId;
  const ownerId = separator >= 0 ? targetBotId.slice(separator + 1) : '';
  return (
    bots.find((bot) => bot.botId === targetBotId) ??
    bots.find((bot) => bot.realBotId === realBotId && (!ownerId || bot.ownerId === ownerId)) ??
    null
  );
}

export function TaskLinkedSessionComposer({
  target,
  userId,
  group,
  onConversationUpdated,
}: {
  target: TaskLinkedSessionTarget;
  userId?: string;
  group: boolean;
  onConversationUpdated: () => void;
}) {
  const storedHumanId = useWorkspaceStore(
    (state) => state.identities.find((identity) => identity.kind === 'user' && !identity.id.startsWith('test-'))?.id,
  );
  const scopeKey = [
    target.kind,
    target.sessionId,
    target.groupId ?? '',
    target.botId ?? '',
    userId ?? '',
    storedHumanId ?? '',
  ].join(':');
  const [draft, setDraft] = useState('');
  const [sending, setSending] = useState(false);
  const [state, setState] = useState<ComposerState>({
    scopeKey,
    phase: 'loading',
    message: '正在准备任务会话…',
  });
  const [mentionConfig, setMentionConfig] = useState<MentionConfig>();
  const providerRef = useRef<({ scopeKey: string } & LinkedSessionProvider) | null>(null);
  const mentionParticipantsRef = useRef<Parameters<typeof expandMentionIds>[1]>([]);
  const botRefreshTimersRef = useRef<Map<number, ReturnType<typeof setTimeout>>>(new Map());
  const mountedRef = useRef(true);

  const clearBotRefreshTimers = useCallback(() => {
    botRefreshTimersRef.current.forEach((timer) => clearTimeout(timer));
    botRefreshTimersRef.current.clear();
  }, []);

  // Bot 会话历史写入存在短暂最终一致性：立即刷新之外再做两次收敛刷新。
  // 流式 onMessage 只补齐尚未排队的刷新，避免连续分片反复推迟已有定时器。
  const scheduleBotConversationRefresh = useCallback(
    (restart = false) => {
      if (restart) clearBotRefreshTimers();
      BOT_CONVERSATION_REFRESH_DELAYS_MS.forEach((delay) => {
        if (botRefreshTimersRef.current.has(delay)) return;
        const timer = setTimeout(() => {
          botRefreshTimersRef.current.delete(delay);
          if (mountedRef.current) onConversationUpdated();
        }, delay);
        botRefreshTimersRef.current.set(delay, timer);
      });
    },
    [clearBotRefreshTimers, onConversationUpdated],
  );

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      clearBotRefreshTimers();
    };
  }, [clearBotRefreshTimers]);

  useEffect(() => {
    let cancelled = false;
    let connection: LinkedSessionProvider | null = null;
    providerRef.current = null;
    mentionParticipantsRef.current = [];
    clearBotRefreshTimers();
    setMentionConfig(undefined);
    setDraft('');
    setSending(false);
    setState({ scopeKey, phase: 'loading', message: '正在准备任务会话…' });

    const disable = (message: string) => {
      if (!cancelled) setState({ scopeKey, phase: 'disabled', message });
    };

    const prepare = async () => {
      try {
        const requesterId = userId?.trim() || storedHumanId?.trim() || '';
        if (!requesterId) {
          disable('缺少当前用户身份，无法向该任务会话发送消息。');
          return;
        }

        if (group) {
          const detail = await sessionService.getSessionDetail(target.sessionId);
          if (cancelled) return;
          if (!detail.ok) throw new Error(detail.error.friendlyMessage);

          const session = detail.data;
          const groupId = session.groupId || target.groupId;
          const identityId = storedHumanId?.trim() || requesterId;
          const human = session.participants.find(
            (participant) =>
              participant.kind === 'human' && isSameHumanIdentity(participant.actorId, requesterId, 'human'),
          );
          const liveEnabled = session.participants.length > 0 && human?.mode !== 'absent';
          mentionParticipantsRef.current = session.participants;
          setMentionConfig(
            buildGroupMentionConfig(session.participants, {
              excludedActorIds: human?.actorId ? [human.actorId] : [],
            }),
          );

          if (!groupId) {
            disable('任务会话缺少群信息，暂时无法发送消息。');
            return;
          }
          if (!liveEnabled) {
            disable(
              human?.mode === 'absent'
                ? '您尚未加入该任务会话，无法发送消息。'
                : '会话成员信息尚未就绪，暂时无法发送消息。',
            );
            return;
          }

          connection = {
            kind: 'group',
            provider: createGroupChatProvider({
              sessionId: target.sessionId,
              groupId,
              identityId,
              liveEnabled: true,
              wsOrigin: resolveGroupWsOrigin(),
            }),
          };
        } else {
          if (!target.botId) {
            disable('任务节点缺少 Bot 信息，暂时无法发送消息。');
            return;
          }
          const result = await botSessionService.listOwnedBots(requesterId);
          if (cancelled) return;
          if (!result.ok) throw new Error(result.error.friendlyMessage);
          const bot = findOwnedBot(result.data, target.botId);
          if (!bot) {
            disable('当前账号没有该 Bot 会话的发送权限。');
            return;
          }
          if (!bot.chatable) {
            disable('当前 Bot 状态不可用，暂时无法发送消息。');
            return;
          }
          connection = {
            kind: 'bot',
            provider: createBotChatProvider({ bot, userId: requesterId, sessionId: target.sessionId }),
          };
        }

        if (cancelled || !connection) {
          connection?.provider.disconnect();
          return;
        }
        providerRef.current = { scopeKey, ...connection };
        const { provider } = connection;
        provider.onMessage = () => {
          if (!cancelled && connection?.kind === 'bot') scheduleBotConversationRefresh();
        };
        provider.onComplete = () => {
          if (cancelled) return;
          onConversationUpdated();
          if (connection?.kind === 'bot') scheduleBotConversationRefresh(true);
        };
        provider.onError = (error) => {
          if (!cancelled) toast.error(error.message || '任务会话发送失败');
        };
        await provider.connect();
        if (cancelled) {
          provider.disconnect();
          return;
        }
        if (connection.kind === 'group') connection.provider.enterLiveMode();
        setState({ scopeKey, phase: 'ready', message: '' });
      } catch (error) {
        if (cancelled) return;
        const message = error instanceof Error ? error.message : '任务会话连接失败，请稍后重试。';
        setState({ scopeKey, phase: 'error', message });
        toast.error(message);
      }
    };

    void prepare();
    return () => {
      cancelled = true;
      clearBotRefreshTimers();
      if (providerRef.current?.scopeKey === scopeKey) providerRef.current = null;
      connection?.provider.disconnect();
    };
  }, [
    clearBotRefreshTimers,
    group,
    onConversationUpdated,
    scheduleBotConversationRefresh,
    scopeKey,
    storedHumanId,
    target.botId,
    target.groupId,
    target.sessionId,
    userId,
  ]);

  const ready = state.scopeKey === scopeKey && state.phase === 'ready';
  const statusMessage = state.scopeKey === scopeKey ? state.message : '正在准备任务会话…';

  const submit = useCallback(
    async (content: string, context?: SubmitContext) => {
      const message = content.trim();
      const current = providerRef.current;
      if (!message || !ready || sending || !current || current.scopeKey !== scopeKey) return;
      setSending(true);
      try {
        if (current.kind === 'group') {
          const mentions = expandMentionIds(context?.mentions ?? [], mentionParticipantsRef.current);
          await current.provider.request({
            content: message,
            sessionId: target.sessionId,
            ...(mentions && mentions.length > 0 ? { mentions } : {}),
          });
        } else {
          await current.provider.request({ content: message, sessionId: target.sessionId });
        }
        if (!mountedRef.current || providerRef.current !== current) return;
        setDraft('');
        onConversationUpdated();
        if (current.kind === 'bot') scheduleBotConversationRefresh(true);
      } catch (error) {
        if (!mountedRef.current || providerRef.current !== current) return;
        toast.error(error instanceof Error ? error.message : '任务会话发送失败，请稍后重试。');
      } finally {
        if (mountedRef.current && providerRef.current === current) setSending(false);
      }
    },
    [onConversationUpdated, ready, scheduleBotConversationRefresh, scopeKey, sending, target.sessionId],
  );

  return (
    <div
      data-testid="task-linked-session-composer"
      className="flex shrink-0 flex-col gap-1.5 border-t border-divider bg-background px-3 py-2 sm:px-6"
    >
      <Sender
        className="sender-shell w-full"
        value={draft}
        onChange={setDraft}
        onSubmit={(content, context) => void submit(content, context)}
        loading={sending}
        disabled={!ready || sending}
        submitType="enter"
        mention={group ? mentionConfig : undefined}
        placeholder={ready ? '输入消息，发送到当前任务节点会话' : statusMessage}
      />
      {!ready && statusMessage ? <p className="m-0 text-xs text-muted-foreground">{statusMessage}</p> : null}
    </div>
  );
}
