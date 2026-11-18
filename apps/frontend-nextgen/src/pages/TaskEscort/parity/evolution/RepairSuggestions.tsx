import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { statusLabels } from '@/domain/taskEscortParity/evolution';
import type { VersionScope } from '@/domain/taskEscortParity/evolutionTypes';
import type { RepairItem } from '@/domain/taskEscortParity/repairTypes';
import type { TaskEscortParityContext } from '@/domain/taskEscortParity/types';
import { useTaskEscortEvolutionRead } from '@/hooks/taskEscortParity/useTaskEscortEvolution';
import { useTaskEscortRepairCandidates } from '@/hooks/taskEscortParity/useTaskEscortRepair';
import { repairService } from '@/services/taskEscortParity/repairService';
import { useCallback, useState } from 'react';
import { JsonDetails, NoData, ReadPager, ReadSelect, ReadState, WriteUnavailable } from './ReadPrimitives';
function SuggestionItem({
  context,
  item,
  preview,
  onOpen,
}: {
  context: TaskEscortParityContext;
  item: RepairItem;
  preview: boolean;
  onOpen?: () => void;
}) {
  const [selected, setSelected] = useState(false),
    [expanded, setExpanded] = useState(false);
  const read = useCallback(
    (signal: AbortSignal) => repairService.item(context.workflowId, item.itemId, context.userId!, signal),
    [context.workflowId, context.userId, item.itemId],
  );
  const detail = useTaskEscortEvolutionRead(context, `repair-original:${item.itemId}`, read, expanded);
  const summary = typeof item.proposal?.summary === 'string' ? item.proposal.summary : item.instruction || item.itemId;
  return (
    <article className="space-y-2 rounded-lg border border-border bg-card p-3">
      <div className="flex items-start gap-2">
        <Input
          type="checkbox"
          className="mt-1 h-4 w-4 shrink-0"
          aria-label={`预览选择建议 ${item.itemId}`}
          checked={selected}
          onChange={(e) => setSelected(e.target.checked)}
          disabled={item.sourceAvailable === false}
        />
        <div className="min-w-0 flex-1">
          <p className="whitespace-pre-wrap break-words text-sm">{summary}</p>
          <p className="mt-1 break-all text-xs text-muted-foreground">
            {statusLabels[item.state ?? ''] ?? item.state ?? '状态未知'} · {item.itemId}
            {item.sourceAvailable === false ? ' · 来源不可用' : ''}
          </p>
        </div>
      </div>
      {selected && <p className="text-xs text-muted-foreground">仅本地只读选择预览，不会生成或应用。</p>}
      <details className="text-xs text-muted-foreground">
        <summary className="cursor-pointer">建议来源（与聚合摘要独立）</summary>
        <pre className="mt-2 whitespace-pre-wrap break-all">{JSON.stringify(item.sources, null, 2)}</pre>
        {item.readSummary && <JsonDetails label="有界来源范围" value={item.readSummary} />}
      </details>
      <Button size="sm" variant="outline" onClick={() => (preview ? onOpen?.() : setExpanded(!expanded))}>
        查看修改与依据
      </Button>
      {expanded && (
        <>
          <ReadState {...detail} />
          {detail.data && (
            <>
              <JsonDetails
                label="原始修改内容"
                value={{ proposal: detail.data.proposal, instruction: detail.data.instruction }}
                open
              />
              <JsonDetails
                label="完整判断依据与来源"
                value={{ sources: detail.data.sources, context: detail.data.context }}
                open
              />
            </>
          )}
        </>
      )}
    </article>
  );
}

export function RepairSuggestions({
  context,
  signature,
  includeHistorical,
  versionScope = 'current',
  snapshotKey,
  preview = false,
  onOpen,
}: {
  context: TaskEscortParityContext;
  signature?: string;
  includeHistorical: boolean;
  versionScope?: VersionScope;
  snapshotKey?: string;
  preview?: boolean;
  onOpen?: () => void;
}) {
  const [page, setPage] = useState(1),
    [state, setState] = useState('all');
  const result = useTaskEscortRepairCandidates(context, {
    versionScope,
    ...(snapshotKey !== undefined ? { snapshotKey } : {}),
    state,
    page,
    pageSize: 20,
    includeHistorical,
    includeTasks: false,
    ...(signature ? { signature } : {}),
  });
  return (
    <section aria-label={preview ? '本问题修复建议预览' : '选择修复建议'} className="space-y-3">
      <ReadState {...result} />
      {result.data && (
        <>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h4 className="text-sm font-medium">
              {preview ? '修复建议' : signature ? '此问题的修复建议' : '全部修复建议'} · 共 {result.data.page.total} 条
            </h4>
            {!preview && (
              <ReadSelect
                label="建议状态"
                value={state}
                onChange={(v) => {
                  setState(v);
                  setPage(1);
                }}
                options={['all', 'pending', 'processing', 'awaiting_verification', 'closed', 'no_action'].map((v) => ({
                  value: v,
                  label: v === 'all' ? '全部状态' : statusLabels[v] ?? v,
                }))}
              />
            )}
          </div>
          {!result.data.items.length && <NoData text="暂无修复建议，等待更多证据或人工判断" />}
          {result.data.items.slice(0, preview ? 3 : 20).map((item) => (
            <SuggestionItem
              key={`${result.data!.inputDigest}:${item.itemId}:${item.contentRevision}:${item.stateVersion}`}
              context={context}
              item={item}
              preview={preview}
              onOpen={onOpen}
            />
          ))}
          {preview ? (
            <Button variant="outline" size="sm" onClick={onOpen}>
              查看此问题全部建议
            </Button>
          ) : (
            <>
              <ReadPager
                label="建议"
                page={page}
                totalPages={result.data.page.totalPages}
                total={result.data.page.total}
                onChange={setPage}
              />
              <WriteUnavailable
                canEdit={result.data.canEdit}
                reason={result.data.capabilities.reason}
                actions={['生成优化任务', '应用建议']}
              />
            </>
          )}
        </>
      )}
    </section>
  );
}
