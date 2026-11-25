import { overviewDuration, overviewTime } from '@/domain/taskEscortParity/overview';
import type { TaskEscortParityContext } from '@/domain/taskEscortParity/types';
import { useTaskEscortRunDetail, useTaskEscortRunWorkflow } from '@/hooks/taskEscortParity/useTaskEscortRunDetail';
import { useMemo, useRef, useState } from 'react';
import { NodeExecutionList } from './NodeExecutionList';
import {
  Button,
  Empty,
  OverviewStatus,
  OverviewTabs,
  ReadFeedback,
  UnavailableActions,
  writeUnavailable,
} from './OverviewShared';
import { RunArchivePanel } from './RunArchivePanel';
import { RunDagView } from './RunDagView';
import { RunLogsPanel } from './RunLogsPanel';

type DetailTab = 'nodes' | 'logs' | 'dag' | 'archive';
export function RunDetail({
  context,
  runId,
  onBack,
}: {
  context: TaskEscortParityContext;
  runId: string;
  onBack: () => void;
}) {
  const detail = useTaskEscortRunDetail(context, runId);
  const [tab, setTab] = useState<DetailTab>('nodes');
  const backRef = useRef<HTMLButtonElement>(null);
  const specContext = { ...context, ready: context.ready && tab === 'dag' && !!detail.data };
  const spec = useTaskEscortRunWorkflow(specContext);
  const run = detail.data?.run,
    nodes = detail.data?.nodes ?? [];
  const latest = useMemo(() => {
    const map = new Map<string, (typeof nodes)[number]>();
    for (const node of nodes)
      if (!map.has(node.nodeId) || (node.attempt ?? 0) > (map.get(node.nodeId)?.attempt ?? 0))
        map.set(node.nodeId, node);
    return [...map.values()];
  }, [detail.data]);
  const succeeded = latest.filter((node) => node.status === 'succeeded').length;
  const failed = latest.filter((node) => node.status === 'failed').length;
  const total = Math.max(run?.nodeCount ?? 0, latest.length);
  return (
    <div className="space-y-4">
      <Button ref={backRef} aria-label="返回运行列表" variant="ghost" size="sm" className="px-0" onClick={onBack}>
        ← 返回运行列表
      </Button>
      <ReadFeedback state={detail} label="运行详情" />
      {!detail.loading && !detail.error && !run && <Empty title="接口未提供运行详情" />}
      {run && (
        <>
          <section aria-label="运行摘要" className="tcov-card p-4">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="min-w-0">
                <h2 className="break-all text-lg font-semibold">{run.workflowTitle || run.workflowId}</h2>
                <p className="mt-1 break-all font-mono text-xs text-muted-foreground">{run.id}</p>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <OverviewStatus status={run.status} />
                <UnavailableActions runId={run.id} />
                <Button variant="outline" size="sm" onClick={detail.refresh}>
                  刷新详情
                </Button>
              </div>
            </div>
            <dl className="mt-4 grid grid-cols-2 gap-x-5 gap-y-3 border-t border-border pt-3 sm:grid-cols-3 lg:grid-cols-7">
              {[
                ['工作流', run.workflowId],
                ['版本', run.version === -1 ? '未绑定发布版本' : run.version ?? '—'],
                ['创建者', run.initiator],
                ['发起 Bot', run.botId ? `${run.botId}${run.pluginVersion ? ` / ${run.pluginVersion}` : ''}` : null],
                ['运行引擎', run.engine],
                ['耗时', overviewDuration(run.durationMs)],
                ['开始时间', overviewTime(run.startedAt)],
              ].map(([label, value]) => (
                <div key={String(label)} className="min-w-0">
                  <dt className="text-[11px] text-muted-foreground">{label}</dt>
                  <dd className="mt-1 break-all text-xs">{value ?? '—'}</dd>
                </div>
              ))}
            </dl>
            <div className="mt-4">
              <div className="mb-2 flex justify-between text-xs text-muted-foreground">
                <span>节点进度</span>
                <span>
                  {succeeded} 成功 / {failed} 失败 / {total} 总计
                </span>
              </div>
              <div className="flex h-2 overflow-hidden rounded-full bg-muted" aria-label="实际节点进度">
                <div className="bg-success" style={{ width: total ? `${(succeeded / total) * 100}%` : 0 }} />
                <div className="bg-destructive" style={{ width: total ? `${(failed / total) * 100}%` : 0 }} />
              </div>
            </div>
            <p className="mt-3 text-[11px] text-muted-foreground">{writeUnavailable}</p>
          </section>
          <section className="tcov-card">
            <OverviewTabs
              label="运行详情页签"
              value={tab}
              options={[
                ['nodes', '节点'],
                ['logs', '日志'],
                ['dag', 'DAG'],
                ['archive', '运行档案'],
              ]}
              onChange={setTab}
            />
            {tab === 'nodes' && <NodeExecutionList nodes={nodes} />}
            {tab === 'logs' && <RunLogsPanel context={context} runId={run.id} />}
            {tab === 'dag' && (
              <>
                <ReadFeedback state={spec} label="工作流规格" />
                {spec.data && <RunDagView workflow={spec.data} nodes={nodes} runVersion={run.version} />}
              </>
            )}
            {tab === 'archive' && <RunArchivePanel context={context} runId={run.id} />}
          </section>
        </>
      )}
    </div>
  );
}
