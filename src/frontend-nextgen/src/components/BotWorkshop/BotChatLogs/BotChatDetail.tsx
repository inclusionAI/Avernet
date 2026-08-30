import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import type { BotChatPage, BotChatRelationScope, BotChatDetail as Detail } from '@/domain/botChats';
import { ArrowLeft } from 'lucide-react';
import { BotChatObservationView } from './BotChatObservationView';

interface Props {
  detail: Detail;
  related?: BotChatPage;
  relationScope: BotChatRelationScope;
  relatedLoading: boolean;
  error?: string;
  botName: string;
  botId: string;
  onBack: () => void;
  onRelation: (scope: BotChatRelationScope) => void;
  onOpenTrace: (traceId: string) => void;
  onLoadMore: () => void;
}

function countObservations(items: Detail['observations']): number {
  return items.reduce((count, item) => count + 1 + countObservations(item.children), 0);
}

export function BotChatDetail({
  detail,
  related,
  relationScope,
  relatedLoading,
  error,
  botName,
  botId,
  onBack,
  onRelation,
  onOpenTrace,
  onLoadMore,
}: Props) {
  return (
    <div className="space-y-4">
      <Button variant="ghost" size="sm" leftIcon={<ArrowLeft className="size-4" />} onClick={onBack}>
        返回日志列表
      </Button>
      <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] p-4">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="m-0 text-base font-semibold text-[var(--color-fg)]">{detail.name}</h2>
          <Badge tone={detail.status === 'SUCCESS' ? 'success' : 'error'}>{detail.status}</Badge>
          <span className="text-xs text-[var(--color-muted)]">
            Trace ID：<span className="font-mono">{detail.id}</span>
          </span>
        </div>
        <dl className="mt-3 grid gap-3 text-xs text-[var(--color-muted)] md:grid-cols-2 xl:grid-cols-6">
          <div>
            <dt>Session</dt>
            <dd className="break-all text-[var(--color-fg)]">{detail.sessionKey ?? detail.sessionId ?? '-'}</dd>
          </div>
          <div>
            <dt>请求时间</dt>
            <dd className="text-[var(--color-fg)]">{new Date(detail.timestamp).toLocaleString()}</dd>
          </div>
          <div>
            <dt>整体耗时</dt>
            <dd className="text-[var(--color-fg)]">{detail.latencyMs.toFixed(0)} ms</dd>
          </div>
          <div>
            <dt>Token</dt>
            <dd className="text-[var(--color-fg)]">{detail.totalTokens}</dd>
          </div>
          <div>
            <dt>成本</dt>
            <dd className="text-[var(--color-fg)]">${detail.totalCost.toFixed(6)}</dd>
          </div>
          <div>
            <dt>Observation</dt>
            <dd className="text-[var(--color-fg)]">{countObservations(detail.observations)}</dd>
          </div>
        </dl>
      </div>
      <BotChatObservationView
        detail={detail}
        related={related}
        relationScope={relationScope}
        relatedLoading={relatedLoading}
        error={error}
        botName={botName}
        botId={botId}
        onRelation={onRelation}
        onOpenTrace={onOpenTrace}
        onLoadMore={onLoadMore}
      />
    </div>
  );
}
