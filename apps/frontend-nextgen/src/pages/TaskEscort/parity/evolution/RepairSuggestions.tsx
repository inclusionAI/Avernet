import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Textarea } from '@/components/ui/Textarea';
import { statusLabels } from '@/domain/taskEscortParity/evolution';
import type { VersionScope } from '@/domain/taskEscortParity/evolutionTypes';
import type { RepairCandidates, RepairItem } from '@/domain/taskEscortParity/repairTypes';
import type { TaskEscortParityContext } from '@/domain/taskEscortParity/types';
import { useTaskEscortEvolutionRead } from '@/hooks/taskEscortParity/useTaskEscortEvolution';
import { useTaskEscortRepairCandidates } from '@/hooks/taskEscortParity/useTaskEscortRepair';
import { useManualRepairGeneration, type GenerationChoice } from '@/hooks/taskEscortParity/useManualRepairGeneration';
import { repairService } from '@/services/taskEscortParity/repairService';
import { useCallback, useState } from 'react';
import { JsonDetails, NoData, ReadPager, ReadSelect, ReadState, WriteUnavailable } from './ReadPrimitives';
function SuggestionItem({
  context,
  item,
  preview,
  onOpen,
  generation,
}: {
  context: TaskEscortParityContext;
  item: RepairItem;
  preview: boolean;
  onOpen?: () => void;
  generation?: GenerationChoice;
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
          aria-label={`${generation ? '选择建议' : '预览选择建议'} ${item.itemId}`}
          checked={generation?.checked ?? selected}
          onChange={(e) => (generation ? generation.toggle() : setSelected(e.target.checked))}
          disabled={generation?.disabled ?? item.sourceAvailable === false}
        />
        <div className="min-w-0 flex-1">
          <p className="whitespace-pre-wrap break-words text-sm">{summary}</p>
          <p className="mt-1 break-all text-xs text-muted-foreground">
            {statusLabels[item.state ?? ''] ?? item.state ?? '状态未知'} · {item.itemId}
            {item.sourceAvailable === false ? ' · 来源不可用' : ''}
          </p>
        </div>
      </div>
      {!generation && selected && <p className="text-xs text-muted-foreground">仅本地只读选择预览，不会生成或应用。</p>}
      {generation?.reason && <p className="text-xs text-muted-foreground">{generation.reason}</p>}
      {generation && generation.count > 1 && (
        <p className="text-xs text-muted-foreground">选择此条建议包含 {generation.count} 条等价原始建议。</p>
      )}
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
        <CandidatePage
          key={result.data.inputDigest}
          context={context}
          data={result.data}
          signature={signature}
          preview={preview}
          onOpen={onOpen}
          manualGeneration={
            !preview && versionScope === 'current' && snapshotKey === undefined && result.data.canOptimize === true
          }
          page={page}
          setPage={setPage}
          state={state}
          setState={setState}
          refresh={result.refresh}
        />
      )}
    </section>
  );
}

function CandidatePage({
  context,
  data,
  signature,
  preview,
  onOpen,
  manualGeneration,
  page,
  setPage,
  state,
  setState,
  refresh,
}: {
  context: TaskEscortParityContext;
  data: RepairCandidates;
  signature?: string;
  preview: boolean;
  onOpen?: () => void;
  manualGeneration: boolean;
  page: number;
  setPage: (page: number) => void;
  state: string;
  setState: (state: string) => void;
  refresh: () => void;
}) {
  const visibleItems = data.items.slice(0, preview ? 3 : 20);
  const { selectionFor, selectedCount, instructions, setInstructions, blockedReason, busy, created, error,
    needsRefresh, isSubmitting, submit } = useManualRepairGeneration(context, data, visibleItems, manualGeneration);
  return (
    <>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h4 className="text-sm font-medium">
          {preview ? '修复建议' : signature ? '此问题的修复建议' : '全部修复建议'} · 共 {data.page.total} 条
        </h4>
        {!preview && (
          <ReadSelect
            label="建议状态"
            value={state}
            onChange={(v) => {
              if (isSubmitting()) return;
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
      {!data.items.length && <NoData text="暂无修复建议，等待更多证据或人工判断" />}
      {visibleItems.map((item, i) => (
        <SuggestionItem
          key={`${item.itemId}:${item.contentRevision}:${item.stateVersion}`}
          context={context}
          item={item}
          preview={preview}
          onOpen={onOpen}
          generation={selectionFor(item.itemId, i)}
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
            totalPages={data.page.totalPages}
            total={data.page.total}
            onChange={(next) => {
              if (!isSubmitting()) setPage(next);
            }}
          />
          {manualGeneration ? (
            <section
              aria-label="生成优化任务"
              className="space-y-2 rounded-lg border border-border bg-muted/40 p-3 text-xs"
            >
              <p>已选 {selectedCount} 条原始建议（仅当前页）。生成候选草稿，不会应用、发布或启动试运行。</p>
              <label className="grid gap-1">
                补充要求（可选）
                <Textarea
                  value={instructions}
                  maxLength={20000}
                  disabled={busy || created || needsRefresh}
                  onChange={(event) => setInstructions(event.target.value)}
                />
              </label>
              {blockedReason && <p className="text-muted-foreground">{blockedReason}</p>}
              <ReadState error={error} refresh={needsRefresh ? refresh : undefined} />
              <div className="flex flex-wrap gap-2">
                <Button
                  size="sm"
                  disabled={!!blockedReason || busy || created || needsRefresh}
                  onClick={() => void submit()}
                >
                  {busy ? '正在提交…' : '生成优化任务'}
                </Button>
                <Button size="sm" disabled>
                  应用建议
                </Button>
              </div>
            </section>
          ) : (
            <WriteUnavailable
              canEdit={data.canEdit}
              reason={data.capabilities.reason}
              actions={['生成优化任务', '应用建议']}
            />
          )}
        </>
      )}
    </>
  );
}
