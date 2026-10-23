import BotAvatar from '@/components/BotWorkshop/BotAvatar';
import { generateBotAvatar } from '@/services/botWorkshop/botAvatarGenerator';
import { DebugChatComposer } from '@/components/BotWorkshop/Editor/DebugChatComposer';
import { DebugAssistantMessage, DebugThinkingReply } from '@/components/BotWorkshop/Editor/DebugChatMessage';
import { BotModelSelector } from '@/components/Workspace/ChatPanel/BotModelSelector';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Empty } from '@/components/ui/Empty';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/Select';
import { Spin } from '@/components/ui/Spin';
import type { BotDomain, BotRuntimeStage } from '@/domain/botWorkshop';
import { useBotChat } from '@/pages/Workspace/hooks/useBotChat';
import { useBotCapabilitySets } from '@/pages/Workspace/hooks/useBotCapabilitySets';
import { useBotModels } from '@/pages/Workspace/hooks/useBotModels';
import { botEditorService } from '@/services/botWorkshop/botEditorService';
import { resolveBotRuntimeStage } from '@/services/botWorkshop/botRuntimeStage';
import { parseDebugChatMessageContent } from '@/services/botWorkshop/debugChatFiles';
import { botSessionService, type BotChatSessionView, type ChatBotView } from '@/services/workspace/botSessionService';
import { FileText, Loader2, Maximize2, Minimize2, Plus, RefreshCw, X } from 'lucide-react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { toast } from 'sonner';

interface DebugMessageLike {
  role: string;
  content?: string;
}

export function shouldAppendThinking(messages: DebugMessageLike[], isRequesting: boolean) {
  if (!isRequesting) return false;
  const lastMessage = messages[messages.length - 1];
  return !lastMessage || lastMessage.role === 'user';
}

export { getDebugMessageBlocks } from '@/components/BotWorkshop/Editor/DebugChatMessage';

function DebugMessageContent({ content }: { content: string }) {
  const parsed = parseDebugChatMessageContent(content);
  return (
    <div className="mt-1 space-y-1.5">
      {parsed.fileNames.map((name, index) => (
        <span
          key={`${name}-${index}`}
          className="flex w-fit max-w-full items-center gap-1.5 rounded-lg border border-border bg-muted px-2 py-1 text-xs"
        >
          <FileText className="size-3.5 shrink-0 text-muted-foreground" aria-hidden />
          <span className="truncate">{name}</span>
        </span>
      ))}
      {parsed.text ? <p className="m-0 whitespace-pre-wrap text-xs leading-5">{parsed.text}</p> : null}
    </div>
  );
}

export function DebugChatPanel({
  bot,
  authenticatedUserId,
  runtimeStage,
  ownerId,
  fullscreen = false,
  onClose,
  onToggleFullscreen,
}: {
  bot: BotDomain;
  authenticatedUserId: string | null;
  runtimeStage?: BotRuntimeStage;
  ownerId?: string;
  fullscreen?: boolean;
  onClose?: () => void;
  onToggleFullscreen?: () => void;
}) {
  const avatarUrl = bot.avatarUrl || generateBotAvatar(bot.id || bot.name);
  const identityId = authenticatedUserId;
  const [sessions, setSessions] = useState<BotChatSessionView[]>([]);
  const [session, setSession] = useState<BotChatSessionView | null>(null);
  const [loading, setLoading] = useState(true);
  const [creatingSession, setCreatingSession] = useState(false);
  const chatBot = useMemo<ChatBotView>(
    () => ({
      botId: bot.id,
      realBotId: bot.id,
      ownerId: ownerId ?? bot.ownerId,
      displayName: bot.name,
      online: bot.lifecycle === 'running',
      chatable: true,
      botType: bot.serviceMode === 'service' ? 'service' : 'personal',
      runtimeStage: bot.serviceMode === 'service' ? runtimeStage ?? resolveBotRuntimeStage(bot.lifecycle) : 'draft',
    }),
    [bot.id, bot.lifecycle, bot.name, bot.ownerId, bot.serviceMode, ownerId, runtimeStage],
  );
  const registerRenderScreenLibraries = useCallback(
    (botId: string) => botEditorService.registerRenderScreenLibraries(botId, ownerId ?? bot.ownerId),
    [bot.ownerId, ownerId],
  );
  const debug = useBotChat(chatBot, session, undefined, registerRenderScreenLibraries, identityId);
  const handleSessionModelChange = useCallback((_botId: string, sessionId: string, model: string) => {
    setSessions((items) => items.map((item) => (item.sessionId === sessionId ? { ...item, model } : item)));
    setSession((current) => (current?.sessionId === sessionId ? { ...current, model } : current));
  }, []);
  const botModels = useBotModels(chatBot, session, identityId, handleSessionModelChange);
  const capabilitySets = useBotCapabilitySets(chatBot, identityId);
  const loadSessions = useCallback(async () => {
    if (!identityId) {
      setLoading(false);
      return;
    }
    setLoading(true);
    const result = await botSessionService.listSessions(chatBot, identityId);
    setLoading(false);
    if (!result.ok) {
      toast.error(result.error.friendlyMessage);
      return;
    }
    setSessions(result.data);
    setSession(
      (current) => result.data.find((item) => item.sessionId === current?.sessionId) ?? result.data[0] ?? null,
    );
  }, [chatBot, identityId]);
  useEffect(() => {
    void loadSessions();
  }, [loadSessions]);
  const create = async () => {
    if (!identityId) return;
    setCreatingSession(true);
    try {
      const result = await botSessionService.createSession(chatBot, identityId, 'Bot 工坊调试');
      if (!result.ok) {
        toast.error(result.error.friendlyMessage);
        return;
      }
      setSessions((items) => [result.data, ...items]);
      setSession(result.data);
    } finally {
      setCreatingSession(false);
    }
  };
  const connection =
    debug.connectionStatus === 'connected'
      ? { text: '在线', tone: 'success' as const }
      : debug.connectionStatus === 'connecting' || debug.connectionStatus === 'reconnecting'
      ? { text: '连接中', tone: 'warning' as const }
      : { text: '未连接', tone: 'neutral' as const };

  return (
    <aside className="flex h-full min-w-0 flex-1 flex-col bg-card">
      <div className="flex min-h-14 items-center gap-3 border-b border-border px-5 py-3">
        <BotAvatar name={bot.name} avatarUrl={avatarUrl} className="size-7 rounded-full" />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <p className="m-0 truncate text-base font-semibold">调试对话</p>
            <Badge tone={connection.tone}>{connection.text}</Badge>
          </div>
          <p className="m-0 mt-1 truncate text-xs text-muted-foreground">
            Human-to-Agent ·{' '}
            {chatBot.runtimeStage === 'online' ? '线上' : chatBot.runtimeStage === 'verify' ? '预发' : '草稿'}环境
          </p>
        </div>
        <Button
          variant="ghost"
          size="icon"
          aria-label="刷新调试会话"
          leftIcon={<RefreshCw className="size-4" />}
          onClick={() => void loadSessions()}
        />
        <Select
          value={session?.sessionId ?? ''}
          onValueChange={(id) => {
            if (id === '__create_session__') {
              void create();
              return;
            }
            setSession(sessions.find((item) => item.sessionId === id) ?? null);
          }}
        >
          <SelectTrigger className="w-44" aria-label="新建或切换调试会话" disabled={creatingSession}>
            <SelectValue placeholder="请选择或新建会话" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="__create_session__">
              <span className="inline-flex items-center gap-2 text-primary">
                {creatingSession ? <Loader2 className="size-3.5 animate-spin" /> : <Plus className="size-3.5" />}
                新建会话
              </span>
            </SelectItem>
            {sessions.map((item) => (
              <SelectItem key={item.sessionId} value={item.sessionId}>
                {item.title}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        {onToggleFullscreen ? (
          <Button
            variant="ghost"
            size="icon"
            aria-label={fullscreen ? '还原调试对话窗口' : '全屏调试对话窗口'}
            aria-pressed={fullscreen}
            leftIcon={fullscreen ? <Minimize2 className="size-4" /> : <Maximize2 className="size-4" />}
            onClick={onToggleFullscreen}
          />
        ) : null}
        {onClose ? (
          <Button
            variant="ghost"
            size="icon"
            aria-label="关闭调试对话"
            leftIcon={<X className="size-4" />}
            onClick={onClose}
          />
        ) : null}
      </div>
      <div className="app-scrollbar min-h-0 flex-1 space-y-6 overflow-y-auto px-6 py-5">
        {loading ? (
          <Spin tip="加载调试会话…" />
        ) : !session ? (
          <Empty compact title="暂无调试会话" description="新建会话后即可向当前 Bot 发送消息。" />
        ) : (
          <>
            {debug.chat.messages.map((message, index) => (
              message.role !== 'user' ? (
                <DebugAssistantMessage
                  key={message.id}
                  message={message}
                  botName={bot.name}
                  avatarUrl={avatarUrl}
                  isStreaming={debug.chat.isRequesting && index === debug.chat.messages.length - 1}
                />
              ) : (
                <div key={message.id} className="group flex items-start gap-3">
                  <div className="flex size-8 shrink-0 items-center justify-center rounded-full bg-brand/15 text-xs text-brand">我</div>
                  <div className="min-w-0 flex-1">
                    <p className="m-0 text-xs font-medium">我</p>
                    <DebugMessageContent content={message.content} />
                  </div>
                </div>
              )
            ))}
            {shouldAppendThinking(debug.chat.messages, debug.chat.isRequesting) ? (
              <DebugThinkingReply botName={bot.name} avatarUrl={avatarUrl} />
            ) : null}
          </>
        )}
      </div>
      <DebugChatComposer
        runtimeStage={chatBot.runtimeStage}
        key={session?.sessionId ?? 'no-session'}
        botId={chatBot.realBotId}
        sessionId={session?.sessionId ?? null}
        userId={identityId}
        ownerId={chatBot.ownerId}
        isRequesting={debug.chat.isRequesting}
        capabilitySets={capabilitySets}
        modelSelector={
          <BotModelSelector
            models={botModels.models}
            activeModelId={botModels.activeModelId}
            loading={botModels.isLoadingModels}
            disabled={!session}
            onSelect={(modelId) => void botModels.selectModel(modelId)}
          />
        }
        onSend={debug.send}
        onStop={debug.stop}
      />
    </aside>
  );
}
