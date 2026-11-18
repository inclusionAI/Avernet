import { Button } from '@/components/ui/Button';
import { buildEditorGraph } from '@/domain/taskEscortParity/editor';
import type { EditorSpec } from '@/domain/taskEscortParity/editorTypes';
import { useId, useMemo, useState } from 'react';
import { EmptyState } from './EditorPrimitives';

export function EditorGraph({
  spec,
  selectedId,
  select,
}: {
  spec: EditorSpec;
  selectedId: string | null;
  select: (id: string) => void;
}) {
  const graph = useMemo(() => buildEditorGraph(spec.nodes), [spec.nodes]);
  const marker = useId().replace(/:/g, '');
  const [zoom, setZoom] = useState(1);
  const width = Math.max(480, ...graph.nodes.map((node) => node.x + 248));
  const height = Math.max(400, ...graph.nodes.map((node) => node.y + 120));
  const positions = new Map(graph.nodes.map((node) => [node.id, node]));
  if (spec.nodes.length === 0)
    return <EmptyState title="工作流没有节点" description="服务端规格的 nodes 为空，未生成示例节点。" />;
  return (
    <section aria-label="工作流画布" className="min-w-0 rounded-lg border border-border bg-background">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border p-3">
        <span className="text-xs text-muted-foreground">
          {spec.nodes.length} 个真实节点 · {graph.edges.length} 条依赖 · 只读画布
        </span>
        <div className="flex items-center gap-2">
          <Button
            variant="ghost"
            size="sm"
            aria-label="缩小画布"
            disabled={zoom <= 0.5}
            onClick={() => setZoom((current) => Math.max(0.5, current - 0.25))}
          >
            −
          </Button>
          <Button variant="ghost" size="sm" aria-label="重置画布缩放" onClick={() => setZoom(1)}>
            {Math.round(zoom * 100)}%
          </Button>
          <Button
            variant="ghost"
            size="sm"
            aria-label="放大画布"
            disabled={zoom >= 1.5}
            onClick={() => setZoom((current) => Math.min(1.5, current + 0.25))}
          >
            +
          </Button>
        </div>
      </div>
      {graph.warnings.length > 0 && (
        <div role="status" className="space-y-1 border-b border-border bg-warning/10 p-3 text-xs">
          {graph.warnings.map((warning) => (
            <p key={warning} className="m-0">
              {warning}
            </p>
          ))}
        </div>
      )}
      <div className="max-h-[640px] overflow-auto p-2" tabIndex={0} aria-label="画布滚动区域">
        <div style={{ width: width * zoom, height: height * zoom }} className="relative">
          <div className="absolute left-0 top-0 origin-top-left" style={{ width, height, transform: `scale(${zoom})` }}>
            <svg
              width={width}
              height={height}
              className="pointer-events-none absolute inset-0 text-muted-foreground"
              aria-label="节点依赖连线"
              role="img"
            >
              <defs>
                <marker
                  id={marker}
                  viewBox="0 0 10 10"
                  refX="9"
                  refY="5"
                  markerWidth="6"
                  markerHeight="6"
                  orient="auto-start-reverse"
                >
                  <path d="M 0 0 L 10 5 L 0 10 z" fill="currentColor" />
                </marker>
              </defs>
              {graph.edges.map((edge) => {
                const from = positions.get(edge.source)!,
                  to = positions.get(edge.target)!;
                const x1 = from.x + 224,
                  y1 = from.y + 44,
                  x2 = to.x,
                  y2 = to.y + 44;
                return (
                  <path
                    key={JSON.stringify(edge)}
                    d={`M ${x1} ${y1} C ${x1 + 24} ${y1}, ${x2 - 24} ${y2}, ${x2} ${y2}`}
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="1.5"
                    markerEnd={`url(#${marker})`}
                  >
                    <title>
                      {edge.source} → {edge.target}
                    </title>
                  </path>
                );
              })}
            </svg>
            {spec.nodes.map((node) => {
              const position = positions.get(node.id)!;
              return (
                <Button
                  key={node.id}
                  variant="outline"
                  aria-label={`${node.title || node.id} · ${node.executor.type}`}
                  aria-pressed={selectedId === node.id}
                  className={`absolute flex h-[88px] w-56 flex-col items-start justify-center gap-1 overflow-hidden border bg-card p-4 text-left shadow-sm ${
                    selectedId === node.id ? 'border-primary ring-2 ring-primary/20' : 'border-border'
                  }`}
                  style={{ left: position.x, top: position.y }}
                  onClick={() => select(node.id)}
                >
                  <span className="w-full truncate text-xs font-semibold">{node.title || node.id}</span>
                  <span className="w-full truncate font-mono text-[10px] text-muted-foreground">
                    {node.id} · {node.executor.type}
                  </span>
                  {typeof node.phase === 'string' && (
                    <span className="truncate text-[10px] text-muted-foreground">{node.phase}</span>
                  )}
                </Button>
              );
            })}
          </div>
        </div>
      </div>
    </section>
  );
}
