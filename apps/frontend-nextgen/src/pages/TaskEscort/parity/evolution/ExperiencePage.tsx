import { Input } from '@/components/ui/Input';
import { displayDate, kindLabels, statusLabels } from '@/domain/taskEscortParity/evolution';
import type { TaskEscortParityContext } from '@/domain/taskEscortParity/types';
import { useTaskEscortEvolutionRead } from '@/hooks/taskEscortParity/useTaskEscortEvolution';
import { evolutionService } from '@/services/taskEscortParity/evolutionService';
import { useCallback, useState } from 'react';
import { EvolutionAccess } from './EvolutionAccess';
import { NoData, ReadSelect, ReadState } from './ReadPrimitives';
function ExperienceRows({ context }: { context: TaskEscortParityContext }) {
  const [status, setStatus] = useState('all'),
    [query, setQuery] = useState('');
  const read = useCallback(
    (signal: AbortSignal) =>
      evolutionService.lessons(
        context.workflowId,
        context.userId!,
        { limit: 100, ...(status !== 'all' ? { status } : {}), ...(query.trim() ? { query: query.trim() } : {}) },
        signal,
      ),
    [context.workflowId, context.userId, status, query],
  );
  const result = useTaskEscortEvolutionRead(context, 'lessons', read);
  return (
    <section aria-label="可复用经验" className="space-y-4 text-foreground">
      <h2 className="text-lg font-semibold">可复用经验</h2>
      <p className="text-sm text-muted-foreground">经验是经过复用边界审核的知识，不由建议应用自动生成。</p>
      <div className="flex flex-wrap items-end gap-3">
        <ReadSelect
          label="经验状态"
          value={status}
          onChange={setStatus}
          options={[
            { value: 'all', label: '全部状态' },
            ...['draft', 'verified', 'published', 'retired'].map((value) => ({ value, label: statusLabels[value] })),
          ]}
        />
        <label className="grid gap-1 text-xs text-muted-foreground">
          查找经验
          <Input
            aria-label="查找经验"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="按签名或经验内容搜索"
          />
        </label>
      </div>
      <ReadState {...result} />
      {result.data && (
        <>
          <p className="text-xs text-muted-foreground">
            共 {result.data.total} 条 · 本次读取上限 100 条。所有经验均为只读。
          </p>
          {!result.data.lessons.length ? (
            <NoData text="还没有可复用经验" />
          ) : (
            <div className="divide-y divide-border rounded-xl border border-border bg-card">
              {result.data.lessons.map((l) => (
                <article key={l.id} className="flex flex-wrap items-start justify-between gap-4 p-4">
                  <div className="min-w-0 flex-1 space-y-2">
                    <p className="flex flex-wrap gap-2">
                      <strong>{kindLabels[l.kind] ?? l.kind}</strong>
                      <span className="rounded bg-muted px-2 text-xs">{statusLabels[l.status] ?? l.status}</span>
                      <span className="text-xs text-muted-foreground">
                        {l.workflowId === context.workflowId
                          ? '当前工作流'
                          : l.workflowId === null
                          ? '全局经验'
                          : l.workflowId}
                      </span>
                    </p>
                    <p className="break-all font-mono text-xs">{l.signature}</p>
                    <p className="break-all text-xs text-muted-foreground">
                      {l.source ?? '来源未知'} · {displayDate(l.createdAt)} · <span>{l.id}</span>
                    </p>
                    <p className="text-xs text-muted-foreground">
                      节点 {l.nodeId ?? '—'} · {l.mode}
                    </p>
                  </div>
                  <dl className="flex gap-6 text-right text-xs">
                    <div>
                      <dt>命中 / 救回</dt>
                      <dd className="mt-1 font-semibold tabular-nums">
                        {l.hits ?? '—'} / {l.rescued ?? '—'}
                      </dd>
                    </div>
                    <div>
                      <dt>成功率</dt>
                      <dd className="mt-1 font-semibold">
                        {l.successRate === null ? '—' : `${Math.round(l.successRate * 100)}%`}
                      </dd>
                    </div>
                  </dl>
                </article>
              ))}
            </div>
          )}
        </>
      )}
    </section>
  );
}

export function ExperiencePage({ context }: { context: TaskEscortParityContext }) {
  return (
    <EvolutionAccess context={context}>
      {() => <ExperienceRows key={`${context.scopeKey}:${context.userId}:${context.workflowId}`} context={context} />}
    </EvolutionAccess>
  );
}
