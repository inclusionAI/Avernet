import { Button } from '@/components/ui';
import { ArrowLeft, ArrowUpRight, Check, Circle } from 'lucide-react';
import { useEffect, useRef } from 'react';
import { DemoBadge, StatusBadge } from './DemoPrimitives';
import { DemoNode, DemoRun, NodeId, RunStatus } from './model';

export function RunDetail({
  run,
  nodes,
  onBack,
  onLocate,
}: {
  run: DemoRun;
  nodes: DemoNode[];
  onBack: () => void;
  onLocate: (id: NodeId) => void;
}) {
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    heading.current?.focus({ preventScroll: true });
  }, [run.id]);
  const active = nodes.findIndex((node) => node.id === run.node);
  const nodeName = nodes[active]?.name ?? run.node;
  return (
    <section aria-label="模拟运行详情" className="p-3.5">
      <div className="flex flex-wrap items-center gap-2">
        <Button variant="ghost" size="sm" onClick={onBack} className="-ml-2">
          <ArrowLeft className="size-3.5" aria-hidden />
          返回列表
        </Button>
        <h3 ref={heading} tabIndex={-1} className="font-mono text-xs font-medium outline-none">
          {run.id}
        </h3>
        <DemoBadge />
        <StatusBadge status={run.status} />
      </div>
      <div className="mt-3 grid grid-cols-3 gap-2 rounded-lg bg-muted/40 p-3 text-[11px]">
        <div>
          <p className="text-muted-foreground">版本</p>
          <p className="mt-1 font-mono">{run.version}</p>
        </div>
        <div>
          <p className="text-muted-foreground">开始时间（模拟）</p>
          <p className="mt-1 font-mono">{run.startedAt}</p>
        </div>
        <div>
          <p className="text-muted-foreground">工作流耗时</p>
          <p className="mt-1 font-mono">{run.duration}</p>
        </div>
      </div>
      <h4 className="mb-2 mt-4 text-xs font-medium">执行路径 · 示意编排</h4>
      <ol className="space-y-1">
        {nodes.map((node, index) => {
          const status: RunStatus =
            run.status === 'success' || index < active ? 'success' : index === active ? run.status : 'queued';
          return (
            <li key={node.id} className="flex items-center gap-3 rounded-lg border border-border/60 px-3 py-2">
              <span className={status === 'success' ? 'text-success' : 'text-muted-foreground'}>
                {status === 'success' ? (
                  <Check className="size-3.5" aria-hidden />
                ) : (
                  <Circle className="size-3.5" aria-hidden />
                )}
              </span>
              <span className="flex-1 text-xs">{node.name}</span>
              <StatusBadge status={status} />
              <Button
                variant="ghost"
                size="sm"
                className="h-6 px-1.5 text-[10px] text-primary"
                onClick={() => onLocate(node.id)}
                aria-label={`在编辑器定位${node.name}`}
              >
                定位
                <ArrowUpRight className="size-3" aria-hidden />
              </Button>
            </li>
          );
        })}
      </ol>
      <p className="mt-3 rounded-lg border border-primary/15 bg-primary/5 px-3 py-2 text-[11px] leading-5 text-muted-foreground">
        {run.status === 'waiting'
          ? `模拟事件：${nodeName}正在等待确认；不会通知真实审批人。`
          : run.status === 'blocked'
          ? `模拟事件：${nodeName}条件尚未满足。可定位该节点，修改本地演示草稿。`
          : '模拟事件：此执行路径仅用于展示运行详情，不代表真实执行结果。'}
      </p>
    </section>
  );
}
