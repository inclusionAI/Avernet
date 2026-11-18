import type { EditorNodeKind, EditorState } from '@/hooks/taskEscortWorkspace/useTaskEscortWorkspaceEditor';
import { Bot, CirclePlay, Database, GitBranch, Send, Settings2, ShieldCheck } from 'lucide-react';
import { useId } from 'react';
import { Button } from './ui';

const nodeIcons = {
  trigger: CirclePlay,
  tool: Database,
  agent: Bot,
  guardian: ShieldCheck,
  output: Send,
  branch: GitBranch,
};
/** Icons belong to presentation; editor snapshots contain only plain, serializable data. */
export function EditorNodeIcon({ kind, size = 15 }: { kind: EditorNodeKind; size?: number }) {
  const Icon = nodeIcons[kind];
  return <Icon size={size} aria-hidden />;
}
export function EditorGraph({ editor }: { editor: EditorState }) {
  const markerId = useId().replace(/:/g, '');
  return (
    <div className="dag-scroll" role="region" aria-label="示例工作流画布" tabIndex={0}>
      <div className="dag-canvas" style={{ zoom: `${editor.zoom}%` }}>
        <svg viewBox="0 0 700 560" className="dag-wires" aria-hidden>
          <defs>
            <marker id={markerId} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5" orient="auto">
              <path d="M0 0L10 5L0 10" fill="none" stroke="hsl(var(--primary)/.5)" strokeWidth="1.5" />
            </marker>
          </defs>
          {editor.connections.map(({ path }) => (
            <path
              key={path}
              d={path}
              stroke="hsl(var(--primary)/.4)"
              strokeWidth="1.8"
              fill="none"
              markerEnd={`url(#${markerId})`}
            />
          ))}
        </svg>
        {editor.nodes.map((node) => (
          <Button
            key={node.id}
            aria-label={`${node.name}节点`}
            aria-pressed={editor.selected === node.id}
            variant="outline"
            className={`dag-node ${editor.selected === node.id ? 'selected' : ''}`}
            style={{ left: node.x, top: node.y }}
            onClick={() => editor.setSelected(node.id)}
          >
            <span
              className={`grid size-7 shrink-0 place-items-center rounded-lg ${
                node.kind === 'guardian' ? 'bg-success/10 text-success' : 'bg-primary/10 text-primary'
              }`}
            >
              <EditorNodeIcon kind={node.kind} />
            </span>
            <span className="min-w-0 flex-1 text-left">
              <span className="block truncate text-[11px] font-medium">{node.name}</span>
              <span className="mt-1 block font-mono text-[9px] font-normal text-muted-foreground">
                {node.kind} · {node.id}
              </span>
            </span>
            <Settings2 size={12} className="text-muted-foreground" aria-hidden />
          </Button>
        ))}
        <span className="dag-branch-label">并行执行</span>
        <div className="dag-minimap" aria-hidden>
          <span />
          <span />
          <span />
          <span />
        </div>
      </div>
    </div>
  );
}
