import { Empty } from '@/components/ui/Empty';
import type { ViewProps } from '@/domain/taskEscortWorkspace';
import { useTaskEscortWorkspaceEditor } from '@/hooks/taskEscortWorkspace/useTaskEscortWorkspaceEditor';
import { ShieldCheck } from 'lucide-react';
import { EditorDialogs } from './EditorDialogs';
import { EditorCanvasControls, EditorToolbar } from './EditorToolbar';
import { EditorWorkspace } from './EditorWorkspace';

function DemoEditor({ demo }: ViewProps) {
  const editor = useTaskEscortWorkspaceEditor(demo);
  return (
    <div className="space-y-4" role="region" aria-label="工作流编辑器" tabIndex={-1} data-detail-focus-fallback>
      <div className="panel !p-0 overflow-hidden">
        <EditorToolbar editor={editor} workflows={demo.workflows} />
        {editor.dirty && !editor.validation.valid && (
          <ul
            role="alert"
            className="space-y-1 border-b border-border bg-destructive/5 px-4 py-2 text-xs text-destructive"
          >
            {editor.validation.errors.map((error) => (
              <li key={error}>{error}</li>
            ))}
          </ul>
        )}
        <EditorWorkspace editor={editor} />
        <EditorCanvasControls editor={editor} />
      </div>
      <p className="flex items-center gap-2 text-[11px] text-muted-foreground">
        <ShieldCheck size={13} className="shrink-0 text-success" aria-hidden />
        演示工作流仅用于交互评审，保存与发布不会触发真实执行。
      </p>
      <EditorDialogs editor={editor} />
    </div>
  );
}
export function Editor({ demo }: ViewProps) {
  if (demo.mode !== 'demo')
    return (
      <Empty
        title="编辑器仅在显式演示模式开放"
        description="真实模式尚未接入工作流配置与发布；未生成示例 DAG，也不会提交变更。"
      />
    );
  return <DemoEditor demo={demo} />;
}
export default Editor;
