import type { TaskEscortParityContext } from '@/domain/taskEscortParity/types';
import { useTaskEscortRunDetail, useTaskEscortRunWorkflow } from '@/hooks/taskEscortParity/useTaskEscortRunDetail';
import { useRef, useState } from 'react';
import { NodeExecutionList } from './NodeExecutionList';
import { Button, Empty, OverviewTabs, ReadFeedback } from './OverviewShared';
import { RunAnalysisCard } from './RunAnalysisCard';
import { RunArchivePanel } from './RunArchivePanel';
import { RunDagView } from './RunDagView';
import { RunInterventionCards } from './RunInterventionCards';
import { RunLogsPanel } from './RunLogsPanel';
import { RunSummaryHeader } from './RunSummaryHeader';
import './runPresentation.css';

type DetailTab = 'nodes' | 'logs' | 'archive';
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
  const evidenceRef = useRef<HTMLElement>(null);
  const spec = useTaskEscortRunWorkflow({ ...context, ready: context.ready && !!detail.data });
  const run = detail.data?.run,
    nodes = detail.data?.nodes ?? [];
  return (
    <div className="run-presentation space-y-4">
      <Button aria-label="返回运行列表" variant="ghost" size="sm" className="px-0" onClick={onBack}>
        ← 返回运行列表
      </Button>
      <ReadFeedback state={detail} label="运行详情" />
      {!detail.loading && !detail.error && !run && <Empty title="接口未提供运行详情" />}
      {run && (
        <>
          <RunSummaryHeader
            key={run.id}
            run={run}
            nodes={nodes}
            onRefresh={detail.refresh}
            onRelatedIssues={() =>
              context.navigate('issues', { runId: run.id, workflowId: context.workflowId })
            }
          />
          <section aria-label="运行 DAG" className="tcov-card">
            <header className="border-b border-border px-4 py-3">
              <h3 className="text-sm font-semibold">运行 DAG</h3>
              <p className="mt-1 text-[11px] text-muted-foreground">查看真实依赖关系，选择节点查看实际执行证据。</p>
            </header>
            <ReadFeedback state={spec} label="工作流规格" />
            {spec.data && <RunDagView key={run.id} workflow={spec.data} nodes={nodes} runVersion={run.version} />}
            {!spec.loading && !spec.error && !spec.data && <Empty compact title="接口未提供工作流规格" />}
          </section>
          <RunInterventionCards />
          <RunAnalysisCard
            status={run.analysisStatus ?? null}
            onEvidence={() => {
              setTab('archive');
              evidenceRef.current?.scrollIntoView?.({ block: 'start', behavior: 'smooth' });
              evidenceRef.current?.focus({ preventScroll: true });
            }}
          />
          <section ref={evidenceRef} tabIndex={-1} aria-label="运行执行证据" className="tcov-card">
            <OverviewTabs
              label="运行详情页签"
              value={tab}
              options={[
                ['nodes', '节点'],
                ['logs', '日志'],
                ['archive', '运行档案'],
              ]}
              onChange={setTab}
            />
            {tab === 'nodes' && <NodeExecutionList key={run.id} nodes={nodes} />}
            {tab === 'logs' && <RunLogsPanel key={run.id} context={context} runId={run.id} />}
            {tab === 'archive' && <RunArchivePanel key={run.id} context={context} runId={run.id} />}
          </section>
        </>
      )}
    </div>
  );
}
