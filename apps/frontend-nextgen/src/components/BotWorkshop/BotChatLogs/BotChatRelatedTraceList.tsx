import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import type { BotChatPage, BotChatRelationScope, BotChatSummary } from '@/domain/botChats';
import { groupBotChatRelatedTraces } from '@/services/botWorkshop/botChatRelations';
import { Bot, LoaderCircle } from 'lucide-react';

interface Props {
  page?: BotChatPage;
  scope: BotChatRelationScope;
  currentTraceId: string;
  botName: string;
  botId: string;
  loading: boolean;
  error?: string;
  onOpenTrace: (traceId: string) => void;
  onLoadMore: () => void;
}

const statusTone = (status: string): 'success' | 'error' | 'warning' | 'neutral' => {
  const normalized = status.toUpperCase();
  if (normalized === 'SUCCESS' || normalized === 'OK') return 'success';
  if (normalized === 'ERROR' || normalized === 'FAILED') return 'error';
  if (normalized === 'RUNNING' || normalized === 'PENDING') return 'warning';
  return 'neutral';
};

function TraceItem({
  item,
  active,
  showBot,
  botName,
  botId,
  onOpen,
}: {
  item: BotChatSummary;
  active: boolean;
  showBot: boolean;
  botName: string;
  botId: string;
  onOpen: () => void;
}) {
  return (
    <Button
      variant="ghost"
      size="sm"
      className={`h-auto w-full justify-start rounded-lg border px-3 py-2 text-left ${
        active
          ? 'border-[var(--color-primary-weak)] bg-[var(--color-primary-soft)]'
          : 'border-transparent hover:border-[var(--color-border)]'
      }`}
      onClick={onOpen}
    >
      <span className="min-w-0 flex-1">
        <span className="flex min-w-0 items-center gap-1.5">
          <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-[var(--color-fg)]">{item.id}</span>
          <Badge tone={statusTone(item.status)}>{item.status}</Badge>
        </span>
        <span className="mt-1 flex flex-wrap items-center gap-1.5 text-[10px] text-[var(--color-muted)]">
          <span>{new Date(item.timestamp).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })}</span>
          {showBot ? (
            <span className="inline-flex items-center gap-1 rounded bg-[var(--color-primary-soft)] px-1.5 py-0.5 text-[var(--color-primary)]">
              <Bot className="size-3" />
              {botName || botId}
            </span>
          ) : null}
          {item.sessionId || item.sessionKey ? (
            <span className="max-w-full truncate rounded bg-[var(--color-panel-strong)] px-1.5 py-0.5 font-mono">
              {item.sessionId || item.sessionKey}
            </span>
          ) : null}
        </span>
      </span>
    </Button>
  );
}

export function BotChatRelatedTraceList({
  page,
  scope,
  currentTraceId,
  botName,
  botId,
  loading,
  error,
  onOpenTrace,
  onLoadMore,
}: Props) {
  const groups = groupBotChatRelatedTraces(page, scope);
  return (
    <aside className="flex min-h-[560px] min-w-0 flex-col border-b border-[var(--color-border)] bg-[var(--color-panel-muted)] lg:border-b-0 lg:border-r">
      <div className="border-b border-[var(--color-border)] px-4 py-3">
        <div className="text-[10px] font-semibold uppercase tracking-[0.16em] text-[var(--color-muted)]">
          关联 Trace
        </div>
        <div className="mt-1 flex items-center justify-between gap-2 text-xs text-[var(--color-muted)]">
          <span>{scope === 'session' ? '按 sessionID' : scope === 'task' ? '按任务ID' : '按群ID'}</span>
          <Badge tone="neutral">共 {page?.total ?? 0} 条</Badge>
        </div>
      </div>
      <div className="app-scrollbar min-h-0 flex-1 overflow-auto p-2">
        {loading && !groups.length ? (
          <div className="flex items-center justify-center gap-2 py-10 text-xs text-[var(--color-muted)]">
            <LoaderCircle className="size-4 animate-spin" />
            正在加载关联 Trace
          </div>
        ) : error ? (
          <p className="px-2 py-8 text-center text-xs text-[var(--color-error)]">{error}</p>
        ) : !groups.length ? (
          <p className="px-2 py-8 text-center text-xs text-[var(--color-muted)]">暂无关联信息</p>
        ) : (
          <div className="space-y-2">
            {groups.map((group) => (
              <div key={group.key}>
                {scope === 'task' ? (
                  <div className="flex items-center gap-2 rounded-md bg-[var(--color-panel-strong)] px-2 py-1 text-[10px] text-[var(--color-muted)]">
                    <span className="min-w-0 flex-1 truncate font-mono">{group.label}</span>
                    <span>{group.items.length} 条</span>
                  </div>
                ) : null}
                <div className={scope === 'task' ? 'mt-1 space-y-1 pl-1' : 'space-y-1'}>
                  {group.items.map((item) => (
                    <TraceItem
                      key={item.id}
                      item={item}
                      active={item.id === currentTraceId}
                      showBot={scope === 'group' || scope === 'task'}
                      botName={item.botName || item.botId || botName}
                      botId={item.botId || botId}
                      onOpen={() => onOpenTrace(item.id)}
                    />
                  ))}
                </div>
              </div>
            ))}
            {page?.hasMore ? (
              <Button variant="ghost" size="sm" className="w-full" disabled={loading} onClick={onLoadMore}>
                {loading ? '正在加载' : '加载更多'}
              </Button>
            ) : null}
          </div>
        )}
      </div>
    </aside>
  );
}
