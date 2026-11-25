import { Button, Empty } from '@/components/ui';
import { ArrowUpRight, CircleAlert, Clock3, ShieldCheck } from 'lucide-react';
import { DemoNode, DemoRun, NodeId } from './model';

export function RunRiskSummary({
  runs,
  nodes,
  onLocate,
  onViewRun,
}: {
  runs: DemoRun[];
  nodes: DemoNode[];
  onLocate: (id: NodeId) => void;
  onViewRun: (id: string) => void;
}) {
  const risks = runs
    .filter((run) => run.status === 'blocked' || run.status === 'waiting')
    .sort((a, b) => Number(b.status === 'blocked') - Number(a.status === 'blocked'));
  return (
    <section aria-label="运行风险摘要" className="rounded-xl border border-border bg-card p-3.5">
      <div className="mb-3 flex items-center gap-2">
        <ShieldCheck className="size-4 text-primary" aria-hidden />
        <h3 className="text-xs font-semibold">运行风险摘要</h3>
        <span className="ml-auto text-[10px] text-muted-foreground">{risks.length} 项演示提示</span>
      </div>
      {risks.length === 0 ? (
        <Empty compact title="当前范围暂无运行风险" description="未发现阻塞或等待中的模拟运行。" />
      ) : (
        <div className="grid gap-3 sm:grid-cols-2">
          {risks.map((run) => {
            const node = nodes.find((item) => item.id === run.node);
            const blocked = run.status === 'blocked';
            const Icon = blocked ? CircleAlert : Clock3;
            return (
              <div
                key={run.id}
                className={`flex items-start gap-2 rounded-lg p-3 ${blocked ? 'bg-destructive/5' : 'bg-warning/5'}`}
              >
                <Icon
                  className={`mt-0.5 size-4 shrink-0 ${blocked ? 'text-destructive' : 'text-muted-foreground'}`}
                  aria-hidden
                />
                <div className="min-w-0 flex-1">
                  <p className="text-xs font-medium">
                    {node?.name ?? run.node}
                    {blocked ? '存在阻塞' : '正在等待'}
                  </p>
                  <p className="mt-1 text-[10px] leading-4 text-muted-foreground">
                    {blocked ? '模拟条件未满足，建议检查节点配置。' : '一条模拟运行停留在此节点，等待确认。'}
                  </p>
                  <code className="mt-1 block break-all text-[9px] text-muted-foreground">{run.id}</code>
                </div>
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-6 shrink-0 px-1 text-[10px] text-primary"
                  onClick={() => (blocked && node ? onLocate(node.id) : onViewRun(run.id))}
                >
                  {blocked && node ? '定位节点' : '查看运行'}
                  <ArrowUpRight className="size-3" aria-hidden />
                </Button>
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}
