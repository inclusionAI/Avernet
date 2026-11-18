import type { Workflow as WorkflowModel } from '@/domain/taskEscortWorkspace';
import type { EditorState } from '@/hooks/taskEscortWorkspace/useTaskEscortWorkspaceEditor';
import { FileCode2, Maximize2, Redo2, Save, Send, Undo2, Workflow, ZoomIn, ZoomOut } from 'lucide-react';
import { Badge, Button, SelectField } from './ui';

export function EditorToolbar({ editor, workflows }: { editor: EditorState; workflows: WorkflowModel[] }) {
  return (
    <>
      <div className="editor-toolbar">
        <div className="flex min-w-0 items-center gap-2">
          <Workflow size={18} className="text-primary" aria-hidden />
          <span className="text-sm font-medium">工作流编排</span>
          <Badge tone="neutral">{editor.version}</Badge>
          {editor.dirty && <span className="text-[10px] text-warning">● 未保存</span>}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <SelectField
            label="编辑工作流"
            value={editor.workflow?.id ?? ''}
            onChange={editor.changeWorkflow}
            options={workflows.map((workflow) => ({ value: workflow.id, label: workflow.name }))}
          />
          <Button variant="outline" onClick={editor.save} aria-label="保存草稿">
            <Save size={14} aria-hidden />
            保存草稿
          </Button>
          <Button onClick={() => editor.setPublish(true)}>
            <Send size={13} aria-hidden />
            模拟发布
          </Button>
        </div>
      </div>
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-4 py-2">
        <div className="flex gap-1">
          <Button
            size="sm"
            variant="ghost"
            className={editor.mode !== 'yaml' ? 'bg-primary/5 text-primary' : ''}
            onClick={() => editor.setMode('visual')}
          >
            <Workflow size={13} aria-hidden />
            工作流编排
          </Button>
          <Button size="sm" variant="ghost" onClick={editor.openScript}>
            <FileCode2 size={13} aria-hidden />
            脚本文件
          </Button>
        </div>
        <div className="flex items-center gap-1 rounded-md bg-muted/60 p-0.5" role="group" aria-label="编辑器视图">
          {editor.modes.map((mode) => (
            <Button
              key={mode.id}
              aria-pressed={editor.mode === mode.id}
              variant="ghost"
              size="sm"
              className={`h-6 px-2 text-[10px] ${editor.mode === mode.id ? 'bg-card shadow-sm' : ''}`}
              onClick={() => editor.setMode(mode.id)}
            >
              {mode.label}
            </Button>
          ))}
        </div>
      </div>
    </>
  );
}
export function EditorCanvasControls({ editor }: { editor: EditorState }) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border px-4 py-2 text-[10px] text-muted-foreground">
      <div className="flex items-center gap-1">
        <Button variant="ghost" size="icon" aria-label="撤销编辑" disabled={!editor.past.length} onClick={editor.undo}>
          <Undo2 size={13} aria-hidden />
        </Button>
        <Button
          variant="ghost"
          size="icon"
          aria-label="重做编辑"
          disabled={!editor.future.length}
          onClick={editor.redo}
        >
          <Redo2 size={13} aria-hidden />
        </Button>
        <span className="ml-2">
          {editor.nodes.length} 个节点 · {editor.connections.length} 条连接
        </span>
      </div>
      <div className="flex items-center gap-1">
        <Button
          variant="ghost"
          size="icon"
          aria-label="缩小画布"
          disabled={editor.zoom <= 60}
          onClick={() => editor.setZoom((zoom) => Math.max(60, zoom - 10))}
        >
          <ZoomOut size={13} aria-hidden />
        </Button>
        <span className="w-9 text-center tabular-nums">{editor.zoom}%</span>
        <Button
          variant="ghost"
          size="icon"
          aria-label="放大画布"
          disabled={editor.zoom >= 150}
          onClick={() => editor.setZoom((zoom) => Math.min(150, zoom + 10))}
        >
          <ZoomIn size={13} aria-hidden />
        </Button>
        <Button variant="ghost" size="icon" aria-label="重置缩放" onClick={() => editor.setZoom(100)}>
          <Maximize2 size={13} aria-hidden />
        </Button>
      </div>
    </div>
  );
}
