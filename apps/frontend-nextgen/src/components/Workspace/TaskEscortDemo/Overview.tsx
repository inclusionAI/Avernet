import { Button, Segmented } from '@/components/ui';
import { Activity, CircleAlert, Clock3, Layers3, ShieldCheck } from 'lucide-react';
import { useState } from 'react';
import { DemoSelect } from './DemoPrimitives';
import { DemoRange, DemoWorkflow, getOverview, NodeId, p95, RunStatus, STATUS_LABEL } from './model';
import { RunList } from './RunList';
import { RunRiskSummary } from './RunRiskSummary';
import { TrendCharts } from './TrendCharts';

export function Overview({ workflow, onLocate }: { workflow: DemoWorkflow; onLocate: (id: NodeId) => void }) {
  const [range, setRange] = useState<DemoRange>('24h');
  const [tab, setTab] = useState<'runs' | 'nodes'>('runs');
  const [focusRun, setFocusRun] = useState<string | null>(null);
  const overview = getOverview(range, workflow.runs);
  const samples = Object.values(workflow.nodeLatencies).flat();
  const hasRuns = overview.runs.length > 0;
  const blocked = overview.runs.filter((run) => run.status === 'blocked').length;
  const health = hasRuns
    ? `${Math.round(((overview.runs.length - overview.abnormal - blocked) / overview.runs.length) * 100)}`
    : '—';
  const metrics = [
    {
      label: '健康度',
      value: health,
      unit: hasRuns ? '/ 100' : '',
      note: '非异常且未阻塞的实例占比',
      icon: ShieldCheck,
    },
    {
      label: '成功率',
      value: overview.successRate,
      unit: '',
      note: '成功 / 终态（成功、失败、终止、取消）',
      icon: Activity,
    },
    { label: '异常结束', value: `${overview.abnormal}`, unit: '次', note: '失败 + 终止 + 取消', icon: CircleAlert },
    {
      label: '节点 P95',
      value: hasRuns && samples.length ? (p95(samples) / 1000).toFixed(2) : '—',
      unit: hasRuns && samples.length ? 's' : '',
      note: '节点执行采样，不含人工等待',
      icon: Clock3,
    },
    {
      label: '总实例',
      value: `${overview.runs.length}`,
      unit: '个',
      note: range === '24h' ? '演示快照 · 最近 24 小时' : '演示快照 · 最近 7 天',
      icon: Layers3,
    },
  ];
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold">工作流运行概览</h2>
          <p className="mt-1 text-[10px] text-muted-foreground">演示快照 · 2026-11-18 14:30（模拟，非实时）</p>
        </div>
        <DemoSelect
          label="概览时间范围"
          value={range}
          onChange={(next) => {
            setRange(next);
            setFocusRun(null);
          }}
          options={[
            { value: '24h', label: '最近 24 小时' },
            { value: '7d', label: '最近 7 天' },
          ]}
        />
      </div>
      <section aria-label="模拟运行指标" className="grid grid-cols-2 gap-2 lg:grid-cols-5">
        {metrics.map(({ label, value, unit, note, icon: Icon }) => (
          <div key={label} className="min-w-0 rounded-xl border border-border bg-card p-3.5">
            <div className="flex items-center justify-between gap-2 text-[11px] text-muted-foreground">
              <span>{label}</span>
              <Icon aria-hidden className="size-3.5" />
            </div>
            <div className="mt-2 flex items-baseline gap-1">
              <strong className="text-2xl font-semibold tracking-tight tabular-nums">{value}</strong>
              <span className="text-[10px] text-muted-foreground">{unit}</span>
            </div>
            <p className="mt-1.5 text-[9px] leading-4 text-muted-foreground">{note}</p>
          </div>
        ))}
      </section>
      <section
        aria-label="模拟实时状态分布"
        className="grid grid-cols-2 gap-2 rounded-xl border border-border bg-card px-4 py-3 sm:grid-cols-4"
      >
        {(['running', 'waiting', 'blocked', 'queued'] as RunStatus[]).map((status) => (
          <div key={status} className="flex items-center gap-2 text-xs">
            <span
              className={
                status === 'running'
                  ? 'size-1.5 rounded-full bg-primary'
                  : 'size-1.5 rounded-full bg-muted-foreground/50'
              }
              aria-hidden
            />
            <span className="text-muted-foreground">{STATUS_LABEL[status]}</span>
            <strong className="ml-auto pr-3 font-mono">
              {overview.runs.filter((run) => run.status === status).length}
            </strong>
          </div>
        ))}
      </section>
      <TrendCharts key={range} range={range} runs={workflow.runs} />
      <RunRiskSummary
        runs={overview.runs}
        nodes={workflow.nodes}
        onLocate={onLocate}
        onViewRun={(id) => {
          setTab('runs');
          setFocusRun(id);
        }}
      />
      <section className="rounded-xl border border-border bg-card">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-3.5 py-3">
          <Segmented
            aria-label="运行分析视图"
            value={tab}
            onChange={setTab}
            options={[
              { value: 'runs', label: '运行列表' },
              { value: 'nodes', label: '节点分析' },
            ]}
            className="w-48"
          />
          <span className="text-[10px] text-muted-foreground">仅模拟数据 · 列表筛选不影响概览</span>
        </div>
        {tab === 'runs' ? (
          <RunList
            runs={overview.runs}
            nodes={workflow.nodes}
            focusRun={focusRun}
            onFocusRun={setFocusRun}
            onLocate={onLocate}
          />
        ) : (
          <div className="overflow-x-auto p-3.5">
            <p className="mb-3 text-[10px] text-muted-foreground">
              独立模拟节点执行采样（毫秒）· 不代表已有运行，不等于工作流耗时，不含人工等待时间。
            </p>
            <table className="w-full text-left text-xs">
              <thead className="text-[10px] text-muted-foreground">
                <tr>
                  <th className="pb-2 font-normal">节点</th>
                  <th className="pb-2 font-normal">采样数</th>
                  <th className="pb-2 font-normal">节点 P95</th>
                  <th className="pb-2 font-normal">操作</th>
                </tr>
              </thead>
              <tbody>
                {workflow.nodes.map((node) => (
                  <tr key={node.id} className="border-t border-border/60">
                    <td className="py-2.5 font-medium">{node.name}</td>
                    <td>{(workflow.nodeLatencies[node.id] ?? []).length}</td>
                    <td className="font-mono">
                      {workflow.nodeLatencies[node.id]?.length ? `${p95(workflow.nodeLatencies[node.id])} ms` : '—'}
                    </td>
                    <td>
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-6 px-1 text-primary"
                        onClick={() => onLocate(node.id)}
                      >
                        编辑节点
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
