import { Drawer, DrawerContent, DrawerDescription, DrawerTitle } from '@/components/ui/Drawer';
import type { OverviewNodeExecution, OverviewWorkflow } from '@/domain/taskEscortParity/overviewTypes';
import { useMemo, useRef, useState } from 'react';
import { Button, Empty, EvidenceValue, OverviewStatus } from './OverviewShared';

/** Layout edges come exclusively from dependsOn; absent execution never becomes a synthetic pending status. */
export function RunDagView({
  workflow,
  nodes,
  runVersion,
}: {
  workflow: OverviewWorkflow;
  nodes: OverviewNodeExecution[];
  runVersion: number | null;
}) {
  const [selected, setSelected] = useState<string | null>(null);
  const openerRef = useRef<HTMLButtonElement | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  const graph = useMemo(() => {
    const ids = new Set(workflow.nodes.map((node) => node.id)),
      levels = new Map<string, number>();
    const visit = (id: string, visiting = new Set<string>()): number => {
      if (levels.has(id)) return levels.get(id)!;
      if (visiting.has(id)) return 0;
      const node = workflow.nodes.find((node) => node.id === id),
        next = new Set(visiting).add(id);
      const level = Math.min(
        workflow.nodes.length,
        Math.max(0, ...(node?.dependsOn.filter((id) => ids.has(id)).map((id) => visit(id, next) + 1) ?? [])),
      );
      levels.set(id, level);
      return level;
    };
    const rows = new Map<number, number>();
    const positioned = workflow.nodes.map((node) => {
      const level = visit(node.id),
        row = rows.get(level) ?? 0;
      rows.set(level, row + 1);
      return { ...node, x: 24 + level * 245, y: 24 + row * 110 };
    });
    return {
      nodes: positioned,
      width: Math.max(300, ...positioned.map((node) => node.x + 230)),
      height: Math.max(190, ...positioned.map((node) => node.y + 100)),
    };
  }, [workflow]);
  const actual = new Map<string, OverviewNodeExecution>();
  for (const node of nodes)
    if (!actual.has(node.nodeId) || (node.attempt ?? 0) > (actual.get(node.nodeId)?.attempt ?? 0))
      actual.set(node.nodeId, node);
  const current = graph.nodes.find((node) => node.id === selected);
  if (!graph.nodes.length) return <Empty compact title="真实工作流规格没有节点" />;
  return (
    <div ref={rootRef} tabIndex={-1} className="min-w-0 space-y-3 p-4">
      <p className="text-[11px] text-muted-foreground">
        DAG 基于当前工作流规格{workflow.version ? ` ${workflow.version}` : ''}；运行绑定版本 {runVersion ?? '未知'}
        ，接口未提供该运行的历史规格快照。节点状态仅来自实际执行记录。
      </p>
      <div className="tcov-scroll rounded-lg border border-border bg-muted/20">
        <div className="relative" style={{ width: graph.width, height: graph.height }}>
          <svg
            aria-label="真实工作流依赖关系"
            role="img"
            width={graph.width}
            height={graph.height}
            className="absolute inset-0 text-border"
          >
            {graph.nodes.flatMap((target) =>
              target.dependsOn.map((id) => {
                const source = graph.nodes.find((node) => node.id === id);
                if (!source) return null;
                return (
                  <path
                    key={`${id}:${target.id}`}
                    d={`M ${source.x + 200} ${source.y + 36} C ${source.x + 225} ${source.y + 36}, ${target.x - 25} ${
                      target.y + 36
                    }, ${target.x} ${target.y + 36}`}
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2"
                  />
                );
              }),
            )}
          </svg>
          {graph.nodes.map((node) => (
            <Button
              key={node.id}
              variant="outline"
              aria-label={`DAG 节点 ${node.title}`}
              onClick={(event) => {
                openerRef.current = event.currentTarget;
                setSelected(node.id);
              }}
              className="absolute h-auto w-[200px] flex-col items-start gap-2 whitespace-normal bg-card px-3 py-3 text-left"
              style={{ left: node.x, top: node.y }}
            >
              <span className="max-w-full truncate text-xs">{node.title}</span>
              <OverviewStatus status={actual.get(node.id)?.status} />
            </Button>
          ))}
        </div>
      </div>
      {current && (
        <Drawer
          open
          onOpenChange={(open) => {
            if (!open) setSelected(null);
          }}
        >
          <DrawerContent
            size="md"
            closeLabel="关闭 DAG 节点"
            className="max-w-full"
            onCloseAutoFocus={(event) => {
              event.preventDefault();
              const opener = openerRef.current;
              const fallback =
                rootRef.current?.querySelector<HTMLButtonElement>('button:not(:disabled)') ?? rootRef.current;
              (opener?.isConnected && !opener.disabled ? opener : fallback)?.focus({ preventScroll: true });
            }}
          >
            <DrawerTitle>{current.title}</DrawerTitle>
            <DrawerDescription>{current.id} · 真实规格与执行状态</DrawerDescription>
            <div className="mt-4 space-y-4">
              <OverviewStatus status={actual.get(current.id)?.status} />
              <EvidenceValue
                label="节点规格"
                value={{ id: current.id, executor: current.executor, dependsOn: current.dependsOn }}
              />
              <EvidenceValue label="执行错误" value={actual.get(current.id)?.error} />
              <EvidenceValue label="实际输出" value={actual.get(current.id)?.output} />
            </div>
          </DrawerContent>
        </Drawer>
      )}
    </div>
  );
}
