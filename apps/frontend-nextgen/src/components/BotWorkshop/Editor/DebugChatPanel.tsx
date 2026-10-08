import BotAvatar from '@/components/BotWorkshop/BotAvatar';
import { DebugChatComposer } from '@/components/BotWorkshop/Editor/DebugChatComposer';
import { BotModelSelector } from '@/components/Workspace/ChatPanel/BotModelSelector';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Empty } from '@/components/ui/Empty';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/Select';
import { Spin } from '@/components/ui/Spin';
import type { BotDomain, BotRuntimeStage } from '@/domain/botWorkshop';
import { useBotChat } from '@/pages/Workspace/hooks/useBotChat';
import { useBotModels } from '@/pages/Workspace/hooks/useBotModels';
import { botEditorService } from '@/services/botWorkshop/botEditorService';
import { resolveBotRuntimeStage } from '@/services/botWorkshop/botRuntimeStage';
import { parseDebugChatMessageContent } from '@/services/botWorkshop/debugChatFiles';
import { botSessionService, type BotChatSessionView, type ChatBotView } from '@/services/workspace/botSessionService';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { FileText, Loader2, Plus, RefreshCw } from 'lucide-react';
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

function ThinkingReply({ botName }: { botName: string }) {
  return (
    <div className="flex items-start gap-3" role="status" aria-live="polite" aria-label={`${botName} 正在思考`}>
      <div className="flex size-8 shrink-0 items-center justify-center rounded-full bg-foreground text-xs text-background">
        {botName.slice(0, 1)}
      </div>
      <div className="min-w-0 flex-1">
        <p className="m-0 text-xs font-medium">{botName}</p>
        <p className="m-0 mt-1 flex items-center gap-1.5 text-xs leading-5 text-muted-foreground">
          <Loader2 className="size-3.5 animate-spin" aria-hidden />
          Thinking...
        </p>
      </div>
    </div>
  );
}

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
  runtimeStage,
  ownerId,
}: {
  bot: BotDomain;
  runtimeStage?: BotRuntimeStage;
  ownerId?: string;
}) {
  const identityId = useWorkspaceStore((state) => state.activeIdentityId);
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
      runtimeStage: runtimeStage ?? resolveBotRuntimeStage(bot.lifecycle),
    }),
    [bot.id, bot.lifecycle, bot.name, bot.ownerId, ownerId, runtimeStage],
  );
  const registerRenderScreenLibraries = useCallback(
    (botId: string) => botEditorService.registerRenderScreenLibraries(botId, ownerId ?? bot.ownerId),
    [bot.ownerId, ownerId],
  );
  const debug = useBotChat(chatBot, session, undefined, registerRenderScreenLibraries);
  const handleSessionModelChange = useCallback((_botId: string, sessionId: string, model: string) => {
    setSessions((items) => items.map((item) => (item.sessionId === sessionId ? { ...item, model } : item)));
    setSession((current) => (current?.sessionId === sessionId ? { ...current, model } : current));
  }, []);
  const botModels = useBotModels(chatBot, session, identityId, handleSessionModelChange);
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
    <aside className="flex h-full min-w-0 flex-1 flex-col border-l border-border bg-card">
      <div className="flex items-center gap-3 border-b border-border p-4">
        <BotAvatar name={bot.name} avatarUrl={bot.avatarUrl} />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <p className="m-0 truncate text-sm font-semibold">调试对话</p>
            <Badge tone={connection.tone}>{connection.text}</Badge>
          </div>
          <p className="m-0 mt-1 truncate text-xs text-muted-foreground">
            Human-to-Agent ·{' '}
            {chatBot.runtimeStage === 'online' ? '线上' : chatBot.runtimeStage === 'verify' ? '预发' : '草稿'}环境
          </p>
        </div>
        <BotModelSelector
          models={botModels.models}
          activeModelId={botModels.activeModelId}
          loading={botModels.isLoadingModels}
          disabled={!session}
          onSelect={(modelId) => void botModels.selectModel(modelId)}
        />
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
      </div>
      <div className="app-scrollbar min-h-0 flex-1 space-y-5 overflow-y-auto p-5">
        {loading ? (
          <Spin tip="加载调试会话…" />
        ) : !session ? (
          <Empty compact title="暂无调试会话" description="新建会话后即可向当前 Bot 发送消息。" />
        ) : (
          <>
            {debug.chat.messages.map((message) => (
              <div key={message.id} className="group flex items-start gap-3">
                <div
                  className={`flex size-8 shrink-0 items-center justify-center rounded-full text-xs ${
                    message.role === 'user' ? 'bg-brand/15 text-brand' : 'bg-foreground text-background'
                  }`}
                >
                  {message.role === 'user' ? '我' : bot.name.slice(0, 1)}
                </div>
                <div className="min-w-0 flex-1">
                  <p className="m-0 text-xs font-medium">{message.role === 'user' ? '我' : bot.name}</p>
                  {debug.chat.isRequesting && message.role !== 'user' && !message.content.trim() ? (
                    <p
                      className="m-0 mt-1 flex items-center gap-1.5 text-xs leading-5 text-muted-foreground"
                      role="status"
                      aria-live="polite"
                    >
                      <Loader2 className="size-3.5 animate-spin" aria-hidden />
                      Thinking...
                    </p>
                  ) : (
                    <DebugMessageContent content={message.content} />
                  )}
                </div>
              </div>
            ))}
            {shouldAppendThinking(debug.chat.messages, debug.chat.isRequesting) ? (
              <ThinkingReply botName={bot.name} />
            ) : null}
          </>
        )}
      </div>
      <DebugChatComposer
        key={session?.sessionId ?? 'no-session'}
        botId={chatBot.realBotId}
        sessionId={session?.sessionId ?? null}
        userId={identityId}
        ownerId={chatBot.ownerId}
        isRequesting={debug.chat.isRequesting}
        onSend={debug.send}
        onStop={debug.stop}
      />
    </aside>
  );
}
