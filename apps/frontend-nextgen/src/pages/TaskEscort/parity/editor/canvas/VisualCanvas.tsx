import {
  Background,
  Controls,
  MiniMap,
  ReactFlow,
  useReactFlow,
  type Connection,
  type Edge,
  type OnNodesChange,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { useEffect, useMemo, useState } from 'react';
import type { EditorGraphProps } from '../EditorGraph';
import {
  branchHandleId,
  branchIdFromHandle,
  canvasTheme,
  canvasThemeClasses,
  GENERAL_SOURCE_HANDLE,
  NODE_DRAG_TYPE,
} from './CanvasPresentation';
import { getResultBranches } from './contracts';
import EditorCanvasToolbar from './EditorCanvasToolbar';
import EditorFlowNode, { type EditorCanvasNode } from './EditorFlowNode';
import { canConnect, computeDagLayout, dependencies, validateSpec } from './graph';
import { connectNodes, deleteNode, removeConnection } from './model';
import { createEditorNode, NODE_CATALOG } from './nodeCatalog';
import NodePalette from './NodePalette';
const nodeTypes = { editorNode: EditorFlowNode };
export default function VisualCanvas(props: EditorGraphProps) {
  const { spec, selectedId, select, onChange, disabled = false } = props;
  const readonly = disabled || !onChange;
  const [positions, setPositions] = useState<Record<string, { x: number; y: number }>>({});
  const [measurements, setMeasurements] = useState<Record<string, { width: number; height: number }>>({});
  const [selectedEdge, setSelectedEdge] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    setSelectedEdge(null);
  }, [selectedId]);
  const { screenToFlowPosition, setCenter, fitView } = useReactFlow<EditorCanvasNode>();
  const layout = useMemo(() => computeDagLayout(spec.nodes), [spec.nodes]);
  const validation = useMemo(() => validateSpec(spec), [spec]);
  const nodes: EditorCanvasNode[] = spec.nodes.map((node) => ({
    id: node.id,
    type: 'editorNode',
    ariaRole: 'button',
    selected: selectedId === node.id && !selectedEdge,
    position:
      positions[node.id] ??
      (typeof node._x === 'number' &&
      Number.isFinite(node._x) &&
      typeof node._y === 'number' &&
      Number.isFinite(node._y)
        ? { x: node._x, y: node._y }
        : layout.get(node.id) ?? { x: 0, y: 0 }),
    measured: measurements[node.id],
    ariaLabel: `${node.title || node.id} · ${node.executor.type}`,
    data: { node, errors: validation.filter((item) => item.nodeId === node.id).map((item) => item.message) },
  }));
  const byId = new Map(spec.nodes.map((node) => [node.id, node]));
  const edges: Edge[] = spec.nodes.flatMap((node) =>
    [...new Set(dependencies(node))].flatMap((source) => {
      const upstream = byId.get(source);
      if (!upstream) return [];
      const branch = getResultBranches(upstream)?.find((item) => item.branchId === node.branchId);
      const id = JSON.stringify([source, node.id]);
      return [
        {
          id,
          source,
          target: node.id,
          selected: selectedEdge === id,
          sourceHandle: branch ? branchHandleId(branch.branchId) : GENERAL_SOURCE_HANDLE,
          label: branch?.branchId,
          ariaLabel: `${upstream.title || source} → ${node.title || node.id}`,
          labelStyle: { fontSize: 11, fill: 'var(--canvas-foreground)' },
          labelBgStyle: { fill: 'var(--canvas-card)' },
          labelBgPadding: [6, 3] as [number, number],
          labelBgBorderRadius: 4,
          interactionWidth: 24,
        },
      ];
    }),
  );
  const choose = (id: string) => {
    setSelectedEdge(null);
    select(id);
  };
  const focus = (id: string) => {
    const node = nodes.find((item) => item.id === id);
    if (node) void setCenter(node.position.x + 120, node.position.y + 70, { zoom: 1, duration: 240 });
  };
  const mutate = (operation: () => typeof spec) => {
    if (readonly) return;
    try {
      onChange?.(operation());
      setError(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  };
  const add = (
    type: string,
    position = screenToFlowPosition({ x: window.innerWidth / 2, y: window.innerHeight / 2 }),
  ) => {
    if (readonly || !NODE_CATALOG.some((item) => item.type === type)) return;
    const node = createEditorNode(type);
    setPositions((previous) => ({ ...previous, [node.id]: position }));
    mutate(() => ({ ...spec, nodes: [...spec.nodes, node] }));
    choose(node.id);
    void setCenter(position.x + 120, position.y + 70, { zoom: 1, duration: 240 });
  };
  const connectionError = (connection: Connection | Edge) => {
    if (readonly) return '画布为只读';
    const branch = branchIdFromHandle(connection.sourceHandle);
    if (connection.sourceHandle && connection.sourceHandle !== GENERAL_SOURCE_HANDLE && branch === undefined)
      return '无法识别的分支端口';
    return canConnect(spec, connection.source, connection.target, branch);
  };
  const onNodesChange: OnNodesChange<EditorCanvasNode> = (changes) => {
    for (const change of changes) {
      if (change.type === 'dimensions' && change.dimensions) {
        const dimensions = change.dimensions;
        setMeasurements((previous) =>
          previous[change.id]?.width === dimensions.width && previous[change.id]?.height === dimensions.height
            ? previous
            : { ...previous, [change.id]: dimensions },
        );
      }
      if (change.type === 'position' && change.position && !readonly) {
        const position = change.position;
        setPositions((previous) => ({ ...previous, [change.id]: position }));
      }
      if (change.type === 'select' && change.selected) choose(change.id);
    }
  };
  const remove = () => {
    const edge = edges.find((item) => item.id === selectedEdge);
    if (edge) mutate(() => removeConnection(spec, edge.source, edge.target));
    else if (selectedId) mutate(() => deleteNode(spec, selectedId));
    setSelectedEdge(null);
  };
  return (
    <section
      onKeyDown={(event) => {
        if (readonly || (event.target as HTMLElement).closest('input, textarea, select, [contenteditable="true"]'))
          return;
        if (!(event.metaKey || event.ctrlKey) || event.key.toLowerCase() !== 'z') return;
        event.preventDefault();
        event.stopPropagation();
        if (event.shiftKey) {
          if (props.canRedo) props.onRedo?.();
        } else if (props.canUndo) props.onUndo?.();
      }}
      aria-label="工作流画布"
      className="flex h-full min-h-[560px] min-w-0 overflow-hidden rounded-lg border border-input bg-background text-foreground"
    >
      <NodePalette disabled={readonly} onAdd={add} />
      <div className="flex min-w-0 flex-1 flex-col">
        <EditorCanvasToolbar
          nodes={spec.nodes}
          canUndo={Boolean(props.canUndo && props.onUndo)}
          canRedo={Boolean(props.canRedo && props.onRedo)}
          onUndo={props.onUndo}
          onRedo={props.onRedo}
          hasPendingEdits={readonly}
          selection={selectedEdge ? 'edge' : selectedId ? 'node' : null}
          onFocus={(id) => {
            choose(id);
            focus(id);
          }}
          onFit={() => {
            void fitView({ padding: 0.2, maxZoom: 1 });
          }}
          onReadableZoom={() => {
            const id = selectedId ?? spec.nodes[0]?.id;
            if (id) focus(id);
          }}
          onDelete={remove}
        />
        {error && (
          <div role="alert" className="border-b border-input bg-destructive/10 p-2 text-xs text-destructive">
            {error}
          </div>
        )}
        {validation.length > 0 && (
          <details className="border-b border-input bg-card p-2 text-xs text-destructive">
            <summary>{validation.length} 项配置问题（原始定义已保留）</summary>
            <ul>
              {validation.map((item, index) => (
                <li key={index}>
                  {item.nodeId}: {item.message}
                </li>
              ))}
            </ul>
          </details>
        )}
        <div
          className="relative min-h-[460px] flex-1"
          data-testid="editor-canvas-dropzone"
          onDragOver={(event) => {
            if (!readonly) {
              event.preventDefault();
              event.dataTransfer.dropEffect = 'copy';
            }
          }}
          onDrop={(event) => {
            event.preventDefault();
            if (!readonly)
              add(
                event.dataTransfer.getData(NODE_DRAG_TYPE),
                screenToFlowPosition({ x: event.clientX, y: event.clientY }),
              );
          }}
        >
          <ReactFlow<EditorCanvasNode, Edge>
            nodes={nodes}
            edges={edges}
            nodeTypes={nodeTypes}
            style={{ ...canvasTheme, position: 'absolute', inset: 0 }}
            className={canvasThemeClasses}
            onNodesChange={onNodesChange}
            nodesDraggable={!readonly}
            nodesConnectable={!readonly}
            edgesReconnectable={false}
            onNodeClick={(_event, node) => choose(node.id)}
            onEdgeClick={(_event, edge) => setSelectedEdge(edge.id)}
            onPaneClick={() => setSelectedEdge(null)}
            onConnect={(connection) => {
              const message = connectionError(connection);
              if (message) setError(message);
              else
                mutate(() =>
                  connectNodes(spec, connection.source, connection.target, branchIdFromHandle(connection.sourceHandle)),
                );
            }}
            isValidConnection={(connection) => connectionError(connection) === null}
            onBeforeDelete={async ({ nodes: removed, edges: removedEdges }) => {
              mutate(() => {
                let next = spec;
                if (removed.length) for (const node of removed) next = deleteNode(next, node.id);
                else for (const edge of removedEdges) next = removeConnection(next, edge.source, edge.target);
                return next;
              });
              return false;
            }}
            deleteKeyCode={readonly ? null : ['Delete', 'Backspace']}
            multiSelectionKeyCode={null}
            selectionKeyCode={null}
            fitView
            fitViewOptions={{ padding: 0.2, maxZoom: 1 }}
            minZoom={0.08}
            maxZoom={2}
            proOptions={{ hideAttribution: true }}
            ariaLabelConfig={{
              'controls.zoomIn.ariaLabel': '放大',
              'controls.zoomOut.ariaLabel': '缩小',
              'controls.fitView.ariaLabel': '适应全部节点',
              'minimap.ariaLabel': '流程小地图',
            }}
          >
            <Background gap={20} size={1} />
            <Controls
              showInteractive={false}
              orientation="horizontal"
              className="overflow-hidden rounded-lg border border-input shadow-sm"
            />
            <MiniMap
              pannable
              zoomable
              aria-label="流程小地图"
              nodeColor={(node) => (node.selected ? 'var(--canvas-primary)' : 'var(--canvas-muted-foreground)')}
              style={{ width: 140, height: 96 }}
            />
          </ReactFlow>
          {!spec.nodes.length && (
            <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
              <div className="rounded-xl border border-dashed border-input bg-card/90 px-8 py-6 text-center">
                <p className="text-sm font-medium">从一个节点开始</p>
                <p className="mt-2 text-xs text-muted-foreground">点击左侧节点，或将它拖到这里</p>
              </div>
            </div>
          )}
        </div>
        <footer className="flex gap-3 border-t border-input bg-card px-3 py-2 text-[10px] text-muted-foreground">
          <span>
            {spec.nodes.length} 个节点 · {edges.length} 条连线
          </span>
          <span className="ml-auto">{readonly ? '只读画布 · 拖动空白处平移' : '拖动空白处平移 · Delete 删除'}</span>
        </footer>
      </div>
    </section>
  );
}
