import BotAvatar from '@/components/BotWorkshop/BotAvatar';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { Empty } from '@/components/ui/Empty';
import { Input } from '@/components/ui/Input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/Select';
import { Spin } from '@/components/ui/Spin';
import type { BotDomain, BotRuntimeStage } from '@/domain/botWorkshop';
import { useBotChat } from '@/pages/Workspace/hooks/useBotChat';
import { botEditorService } from '@/services/botWorkshop/botEditorService';
import { resolveBotRuntimeStage } from '@/services/botWorkshop/botRuntimeStage';
import { botSessionService, type BotChatSessionView, type ChatBotView } from '@/services/workspace/botSessionService';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { Loader2, Plus, RefreshCw, Send, Square } from 'lucide-react';
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

export function DebugChatPanel({ bot, runtimeStage }: { bot: BotDomain; runtimeStage?: BotRuntimeStage }) {
  const identityId = useWorkspaceStore((state) => state.activeIdentityId);
  const [sessions, setSessions] = useState<BotChatSessionView[]>([]);
  const [session, setSession] = useState<BotChatSessionView | null>(null);
  const [draft, setDraft] = useState('');
  const [loading, setLoading] = useState(true);
  const chatBot = useMemo<ChatBotView>(
    () => ({
      botId: bot.id,
      realBotId: bot.id,
      ownerId: bot.ownerId,
      displayName: bot.name,
      online: bot.lifecycle === 'running',
      chatable: true,
      runtimeStage: runtimeStage ?? resolveBotRuntimeStage(bot.lifecycle),
    }),
    [bot.id, bot.lifecycle, bot.name, bot.ownerId, runtimeStage],
  );
  const debug = useBotChat(chatBot, session, undefined, botEditorService.registerRenderScreenLibraries);
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
    const result = await botSessionService.createSession(chatBot, identityId, 'Bot 工坊调试');
    if (!result.ok) {
      toast.error(result.error.friendlyMessage);
      return;
    }
    setSessions((items) => [result.data, ...items]);
    setSession(result.data);
  };
  const send = () => {
    if (!draft.trim()) return;
    debug.send(draft);
    setDraft('');
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
        <BotAvatar name={bot.name} />
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
        <Button
          variant="ghost"
          size="icon"
          aria-label="刷新调试会话"
          leftIcon={<RefreshCw className="size-4" />}
          onClick={() => void loadSessions()}
        />
        <Button variant="secondary" size="sm" leftIcon={<Plus className="size-4" />} onClick={() => void create()}>
          新建会话
        </Button>
      </div>
      <div className="border-b border-border p-3">
        <Select
          value={session?.sessionId ?? ''}
          onValueChange={(id) => setSession(sessions.find((item) => item.sessionId === id) ?? null)}
        >
          <SelectTrigger aria-label="调试会话">
            <SelectValue placeholder="请选择或新建会话" />
          </SelectTrigger>
          <SelectContent>
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
                    <p className="m-0 mt-1 whitespace-pre-wrap text-xs leading-5">{message.content}</p>
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
      <div className="border-t border-border p-4">
        <Card className="flex items-center gap-2 rounded-2xl p-2 shadow-none">
          <Input
            value={draft}
            disabled={!session}
            placeholder="输入调试消息，Enter 发送"
            className="border-0 shadow-none focus-visible:ring-0"
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') send();
            }}
          />
          <Button
            size="icon"
            disabled={!session || (!debug.chat.isRequesting && !draft.trim())}
            aria-label={debug.chat.isRequesting ? '停止生成' : '发送'}
            leftIcon={debug.chat.isRequesting ? <Square className="size-4" /> : <Send className="size-4" />}
            onClick={debug.chat.isRequesting ? debug.stop : send}
          />
        </Card>
      </div>
    </aside>
  );
}
