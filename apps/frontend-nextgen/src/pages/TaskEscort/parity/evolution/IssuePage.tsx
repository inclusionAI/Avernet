import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Modal, ModalContent, ModalDescription, ModalTitle } from '@/components/ui/Modal';
import { displayDate, snapshotLabel } from '@/domain/taskEscortParity/evolution';
import type { IssueGroup, IssueQuery, VersionScope } from '@/domain/taskEscortParity/evolutionTypes';
import type { TaskEscortParityContext } from '@/domain/taskEscortParity/types';
import { useTaskEscortEvolutionRead } from '@/hooks/taskEscortParity/useTaskEscortEvolution';
import { useTaskEscortRepairCandidates } from '@/hooks/taskEscortParity/useTaskEscortRepair';
import { evolutionService } from '@/services/taskEscortParity/evolutionService';
import { useCallback, useState } from 'react';
import { EvolutionAccess } from './EvolutionAccess';
import { HistoricalIssues } from './HistoricalIssues';
import { IssueDrawer } from './IssueDrawer';
import { NoData, ReadCapabilities, ReadPager, ReadSelect, ReadState, WriteUnavailable } from './ReadPrimitives';
import { RepairSuggestions } from './RepairSuggestions';
import { RepairTaskList } from './RepairTaskList';
import { RepairTaskWorkspace } from './RepairTaskWorkspace';
function IssueRow({
  context,
  group: g,
  includeHistorical,
  onOpen,
}: {
  context: TaskEscortParityContext;
  group: IssueGroup;
  includeHistorical: boolean;
  onOpen: (tab: 'causes' | 'repairs' | 'evidence') => void;
}) {
  return (
    <article className="space-y-3 rounded-lg border border-border p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm font-medium">
          节点 {g.sources[0]?.nodeId ?? '工作流整体'} · {g.sources[0]?.failureMode ?? '模式未知'}
        </p>
        <Button variant="outline" size="sm" aria-label={`查看问题 ${g.signature}`} onClick={() => onOpen('causes')}>
          查看问题
        </Button>
      </div>
      <p className="text-xs text-muted-foreground">
        影响：
        {({ critical: '严重', high: '高', medium: '中', low: '低' } as Record<string, string>)[
          g.priority?.severity ?? ''
        ] ?? '待评估'}{' '}
        · 近24小时发现 {g.priority?.day.affected ?? '—'} 次 · 近7天发现 {g.priority?.week.affected ?? '—'} 次 · 最近发生{' '}
        {displayDate(g.priority?.lastOccurredAtMs)}
      </p>
      <p className="whitespace-pre-wrap break-words text-sm">
        {g.summary?.summary ?? '摘要尚未生成，请查看单次诊断原因'}
      </p>
      {g.stale && <p className="text-xs text-warning-foreground">摘要待更新</p>}
      <p className="break-all font-mono text-xs text-muted-foreground">{g.signature}</p>
      <RepairSuggestions
        context={context}
        signature={g.signature}
        includeHistorical={includeHistorical}
        preview
        onOpen={() => onOpen('repairs')}
      />
    </article>
  );
}

function IssueContents({ context, canEdit }: { context: TaskEscortParityContext; canEdit: boolean }) {
  const [query, setQuery] = useState<IssueQuery>({ versionScope: 'current', page: 1, pageSize: 10, sort: 'priority' });
  const [includeHistorical, setIncludeHistorical] = useState(false),
    [allSuggestions, setAllSuggestions] = useState(false);
  const [selection, setSelection] = useState<{
    signature: string;
    tab?: 'causes' | 'repairs' | 'evidence';
    scope: VersionScope;
    snapshotKey?: string;
  } | null>(null);
  const [closedLink, setClosedLink] = useState('');
  const linkKey = [context.query.issueSignature, context.query.analysisId, context.query.runId].join(':');
  const linkActive =
    linkKey !== closedLink && !!(context.query.issueSignature || context.query.analysisId || context.query.runId);
  const lookupRead = useCallback(
    (signal: AbortSignal) =>
      evolutionService.diagnoses(
        context.workflowId,
        context.userId!,
        {
          ...(context.query.analysisId ? { analysisId: context.query.analysisId } : {}),
          ...(context.query.runId ? { flowId: context.query.runId } : {}),
          limit: 10,
          offset: 0,
        },
        signal,
      ),
    [context.workflowId, context.userId, context.query.analysisId, context.query.runId],
  );
  const lookup = useTaskEscortEvolutionRead(
    context,
    'deep-link-diagnosis',
    lookupRead,
    linkActive && !context.query.issueSignature,
  );
  const linkedSignature = linkActive ? context.query.issueSignature ?? lookup.data?.diagnoses[0]?.signature : undefined;
  const selected =
    selection ??
    (linkedSignature
      ? { signature: linkedSignature, scope: 'all' as const, tab: 'evidence' as const, snapshotKey: undefined }
      : null);
  const key = JSON.stringify(query);
  const read = useCallback(
    (signal: AbortSignal) => evolutionService.groups(context.workflowId, context.userId!, JSON.parse(key), signal),
    [context.workflowId, context.userId, key],
  );
  const issues = useTaskEscortEvolutionRead(context, `issue-list:${key}`, read);
  const repairs = useTaskEscortRepairCandidates(context, {
    versionScope: 'current',
    state: 'all',
    page: 1,
    pageSize: 20,
    includeHistorical,
    includeTasks: true,
  });
  const filter = (name: 'sort' | 'severity' | 'nodeId' | 'failureMode', value: string) =>
    setQuery((q) => ({ ...q, [name]: value === 'all' ? undefined : value, page: 1 }));
  const taskId = context.query.repairTaskId;
  if (taskId) return <RepairTaskWorkspace context={context} taskId={taskId} canEdit={canEdit} />;
  return (
    <section aria-label="问题与优化" className="space-y-6 text-foreground">
      <ReadState {...repairs} />
      {repairs.data && <RepairTaskList context={context} data={repairs.data} />}
      <section className="space-y-4 rounded-xl border border-border bg-card p-4">
        <header className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="text-lg font-semibold">当前版本问题</h2>
            <p className="mt-1 text-xs text-muted-foreground">
              {snapshotLabel(repairs.data?.activeSnapshot)} · 不把观察次数当作失败率
            </p>
          </div>
          <Button variant="outline" size="sm" onClick={() => setAllSuggestions(true)}>
            查找全部建议
          </Button>
        </header>
        <label className="flex items-center gap-2 text-sm">
          <Input
            type="checkbox"
            className="h-4 w-4"
            checked={includeHistorical}
            onChange={(e) => setIncludeHistorical(e.target.checked)}
          />
          包含更早的建议
        </label>
        <div className="flex flex-wrap items-end gap-3">
          <ReadSelect
            label="问题排序"
            value={query.sort ?? 'priority'}
            onChange={(v) => filter('sort', v)}
            options={[
              { value: 'priority', label: '优先级' },
              { value: 'frequency', label: '发生频次' },
              { value: 'recent', label: '最近发生' },
            ]}
          />
          <ReadSelect
            label="影响等级"
            value={query.severity ?? 'all'}
            onChange={(v) => filter('severity', v)}
            options={['all', 'critical', 'high', 'medium', 'low'].map((v, i) => ({
              value: v,
              label: ['全部等级', '严重', '高', '中', '低'][i],
            }))}
          />
          <ReadSelect
            label="问题节点"
            value={query.nodeId ?? 'all'}
            onChange={(v) => filter('nodeId', v)}
            options={[
              { value: 'all', label: '全部节点' },
              ...(issues.data?.facets.nodes ?? []).map((value) => ({ value, label: value })),
            ]}
          />
          <ReadSelect
            label="失败模式"
            value={query.failureMode ?? 'all'}
            onChange={(v) => filter('failureMode', v)}
            options={[
              { value: 'all', label: '全部问题类型' },
              ...(issues.data?.facets.modes ?? []).map((value) => ({ value, label: value })),
            ]}
          />
        </div>
        <ReadState {...issues} />
        {issues.data && (
          <>
            {!issues.data.groups.length && <NoData text="当前版本暂无问题" />}
            {issues.data.groups.map((g) => (
              <IssueRow
                key={g.signature}
                group={g}
                context={context}
                includeHistorical={includeHistorical}
                onOpen={(tab) => setSelection({ signature: g.signature, tab, scope: 'current' })}
              />
            ))}
            <ReadPager
              label="问题"
              page={query.page}
              totalPages={issues.data.page.totalPages}
              total={issues.data.page.total}
              pageSize={query.pageSize}
              onSize={(pageSize) => setQuery((q) => ({ ...q, pageSize, page: 1 }))}
              onChange={(page) => setQuery((q) => ({ ...q, page }))}
            />
          </>
        )}
      </section>
      {repairs.data && (
        <ReadCapabilities capabilities={repairs.data.capabilities} canEdit={canEdit && repairs.data.canEdit} />
      )}
      {repairs.data?.canOptimize === true ? (
        <p className="text-xs text-muted-foreground">
          在当前版本的修复建议中选择条目后，可提交草稿生成，不会应用或发布。
        </p>
      ) : (
        <WriteUnavailable canEdit={canEdit && !!repairs.data?.canEdit} reason={repairs.data?.capabilities.reason} />
      )}
      <HistoricalIssues
        context={context}
        onOpen={(signature, snapshotKey) => setSelection({ signature, snapshotKey, scope: 'historical' })}
      />
      {linkActive && !context.query.issueSignature && (
        <>
          <ReadState {...lookup} />
          {lookup.data && !lookup.data.diagnoses.length && (
            <NoData text="所选运行/分析未找到关联问题，不会选择其他问题" />
          )}
        </>
      )}
      {selected && (
        <IssueDrawer
          key={`${selected.signature}:${selected.tab}:${selected.scope}:${selected.snapshotKey}:${linkKey}`}
          context={context}
          signature={selected.signature}
          initialTab={selected.tab}
          versionScope={selected.scope}
          snapshotKey={selected.snapshotKey}
          includeHistorical={includeHistorical}
          onClose={() => {
            setSelection(null);
            setClosedLink(linkKey);
            context.navigate('issues', { issueSignature: undefined, analysisId: undefined, runId: undefined });
          }}
        />
      )}
      <Modal open={allSuggestions} onOpenChange={setAllSuggestions}>
        <ModalContent size="xl">
          <ModalTitle>查找全部建议</ModalTitle>
          <ModalDescription>状态与分页独立于问题列表。完整修改和依据由原始材料接口读取。</ModalDescription>
          {allSuggestions && <RepairSuggestions context={context} includeHistorical={includeHistorical} />}
        </ModalContent>
      </Modal>
    </section>
  );
}

export function IssuePage({ context }: { context: TaskEscortParityContext }) {
  return (
    <EvolutionAccess context={context}>
      {(canEdit) => (
        <IssueContents
          key={`${context.scopeKey}:${context.userId}:${context.workflowId}`}
          context={context}
          canEdit={canEdit}
        />
      )}
    </EvolutionAccess>
  );
}
