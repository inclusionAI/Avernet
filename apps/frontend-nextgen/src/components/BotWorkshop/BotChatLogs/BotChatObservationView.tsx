import { Card } from '@/components/ui/Card';
import { Segmented } from '@/components/ui/Segmented';
import type { BotChatDetail, BotChatPage, BotChatRelationScope } from '@/domain/botChats';
import { useEffect, useState } from 'react';
import { BotChatNodeDetails } from './BotChatNodeDetails';
import { BotChatRelatedTraceList } from './BotChatRelatedTraceList';
import { BotChatSelection, BotChatTimeline } from './BotChatTimeline';

interface Props {
  detail: BotChatDetail;
  related?: BotChatPage;
  relationScope: BotChatRelationScope;
  relatedLoading: boolean;
  error?: string;
  botName: string;
  botId: string;
  onRelation: (scope: BotChatRelationScope) => void;
  onOpenTrace: (traceId: string) => void;
  onLoadMore: () => void;
}

function RelationSegmented({
  detail,
  value,
  onChange,
}: {
  detail: BotChatDetail;
  value: BotChatRelationScope;
  onChange: (scope: BotChatRelationScope) => void;
}) {
  return (
    <div className="flex flex-wrap items-center gap-3">
      <span className="text-[10px] font-semibold uppercase tracking-[0.16em] text-[var(--color-muted)]">关联维度</span>
      <Segmented
        value={value}
        options={[
          {
            value: 'session',
            label: '按 sessionID',
            disabledReason:
              detail.sessionId || detail.sessionKey
                ? undefined
                : '当前 Trace 无 sessionID / sessionKey，无法按该维度查询',
          },
          {
            value: 'task',
            label: '按任务ID',
            disabledReason: detail.bizTaskId ? undefined : '当前 Trace 无任务ID，无法按该维度查询',
          },
          {
            value: 'group',
            label: '按群ID',
            disabledReason: detail.groupId ? undefined : '当前 Trace 无群ID，无法按该维度查询',
          },
        ]}
        onChange={onChange}
        className="w-fit"
      />
    </div>
  );
}

function countObservations(items: BotChatDetail['observations']): number {
  return items.reduce((count, item) => count + 1 + countObservations(item.children), 0);
}

export function BotChatObservationView({
  detail,
  related,
  relationScope,
  relatedLoading,
  error,
  botName,
  botId,
  onRelation,
  onOpenTrace,
  onLoadMore,
}: Props) {
  const [selection, setSelection] = useState<BotChatSelection>({ kind: 'trace' });
  useEffect(() => setSelection({ kind: 'trace' }), [detail.id]);

  return (
    <Card className="overflow-hidden shadow-sm">
      <div className="flex flex-wrap items-center gap-4 border-b border-[var(--color-border)] px-4 py-3">
        <div className="flex items-center gap-2 text-xs text-[var(--color-muted)]">
          <span>sessionID：</span>
          <strong className="max-w-[220px] truncate font-mono text-[var(--color-fg)]">
            {detail.sessionId || detail.sessionKey || '-'}
          </strong>
        </div>
        <div className="flex items-center gap-2 text-xs text-[var(--color-muted)]">
          <span>任务ID：</span>
          <strong className="max-w-[220px] truncate font-mono text-[var(--color-fg)]">{detail.bizTaskId || '-'}</strong>
        </div>
        <div className="flex items-center gap-2 text-xs text-[var(--color-muted)]">
          <span>群ID：</span>
          <strong className="max-w-[220px] truncate font-mono text-[var(--color-fg)]">{detail.groupId || '-'}</strong>
        </div>
        <div className="ml-auto flex items-center gap-2 text-xs text-[var(--color-muted)]">
          <span>关联 Trace：{related?.total ?? 0}</span>
          <span>Observation：{countObservations(detail.observations)}</span>
        </div>
      </div>
      <div className="border-b border-[var(--color-border)] px-4 py-3">
        <RelationSegmented detail={detail} value={relationScope} onChange={onRelation} />
      </div>
      <div className="grid lg:grid-cols-[280px_300px_minmax(0,1fr)]">
        <BotChatRelatedTraceList
          page={related}
          scope={relationScope}
          currentTraceId={detail.id}
          botName={botName}
          botId={botId}
          loading={relatedLoading}
          error={error}
          onOpenTrace={onOpenTrace}
          onLoadMore={onLoadMore}
        />
        <BotChatTimeline detail={detail} selection={selection} onSelect={setSelection} />
        <BotChatNodeDetails detail={detail} selection={selection} />
      </div>
    </Card>
  );
}
