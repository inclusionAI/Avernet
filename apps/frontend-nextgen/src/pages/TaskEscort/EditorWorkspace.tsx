import type { EditorState } from '@/hooks/taskEscortWorkspace/useTaskEscortWorkspaceEditor';
import { Braces, ShieldCheck } from 'lucide-react';
import { EditorGraph, EditorNodeIcon } from './EditorGraph';
import { Badge, Button, Input, Textarea } from './ui';

function EditorNodePalette({ editor }: { editor: EditorState }) {
  return (
    <aside className="node-palette" aria-label="节点组件">
      <p className="mb-4 text-xs font-medium">节点组件</p>
      {editor.palette.map((item) => (
        <Button
          key={item.name}
          variant="outline"
          disabled={item.nodeId === null}
          className="mb-2.5 h-auto w-full justify-start gap-2 px-2.5 py-3 text-left"
          onClick={() => {
            if (item.nodeId) editor.selectPalette(item.nodeId);
          }}
        >
          <span className="shrink-0 text-primary">
            <EditorNodeIcon kind={item.kind} size={16} />
          </span>
          <span>
            <span className="block text-[11px]">{item.name}</span>
            <span className="mt-1 block text-[9px] font-normal text-muted-foreground">{item.desc}</span>
          </span>
        </Button>
      ))}
      <p className="mt-5 text-[10px] leading-5 text-muted-foreground">
        点击组件查看示例节点
        <br />
        编辑属性后可保存草稿
      </p>
    </aside>
  );
}
function EditorYamlPreview({ yaml }: { yaml: string }) {
  return (
    <div className="yaml-pane">
      <div className="mb-3 flex items-center justify-between text-[10px] text-muted-foreground">
        <span className="flex items-center gap-1">
          <Braces size={13} aria-hidden />
          workflow.yaml
        </span>
        <Badge tone="neutral">只读预览</Badge>
      </div>
      <pre data-testid="editor-yaml" className="font-mono text-[11px] leading-6">
        <code>{yaml}</code>
      </pre>
      <p className="mt-4 text-[10px] leading-5 text-muted-foreground">
        YAML 与可视化节点属性保持同步。本轮不执行脚本或任意代码。
      </p>
    </div>
  );
}
function EditorNodeProperties({ editor }: { editor: EditorState }) {
  const { node, patch } = editor;
  return (
    <aside className="node-properties" aria-label="节点属性">
      <div className="mb-5 flex items-center justify-between">
        <h3 className="text-xs font-medium">节点属性</h3>
        <Badge tone="primary">{node.kind}</Badge>
      </div>
      <label className="field-label" htmlFor="node-name">
        节点名称
      </label>
      <Input
        id="node-name"
        value={node.name}
        aria-invalid={!node.name.trim()}
        onChange={(event) => patch({ name: event.target.value })}
      />
      <label className="field-label mt-4" htmlFor="node-id">
        节点 ID
      </label>
      <Input id="node-id" value={node.id} readOnly className="font-mono text-muted-foreground" />
      <label className="field-label mt-4" htmlFor="node-description">
        执行说明
      </label>
      <Textarea
        id="node-description"
        value={node.description}
        onChange={(event) => patch({ description: event.target.value })}
        className="min-h-24 text-xs leading-6"
      />
      <div className="mt-4 grid grid-cols-2 gap-3">
        <div>
          <label className="field-label" htmlFor="node-timeout">
            超时 / 秒
          </label>
          <Input
            id="node-timeout"
            type="number"
            min="1"
            max="3600"
            step="1"
            value={node.timeout}
            onChange={(event) => patch({ timeout: event.target.value })}
          />
        </div>
        <div>
          <label className="field-label" htmlFor="node-retries">
            重试次数
          </label>
          <Input
            id="node-retries"
            type="number"
            min="0"
            max="10"
            step="1"
            value={node.retries}
            onChange={(event) => patch({ retries: event.target.value })}
          />
        </div>
      </div>
      <div className="mt-6 rounded-lg bg-primary/5 p-3">
        <p className="flex items-center gap-2 text-xs font-medium text-primary">
          <ShieldCheck size={14} aria-hidden />
          护航建议
        </p>
        <p className="mt-2 text-[10px] leading-5 text-muted-foreground">
          对外部数据调用设置合理超时与退避重试，可减少瞬时故障带来的运行中断。
        </p>
      </div>
    </aside>
  );
}

export function EditorWorkspace({ editor }: { editor: EditorState }) {
  return (
    <div className={`editor-workspace ${editor.mode === 'yaml' ? 'yaml-mode' : ''}`}>
      {editor.mode !== 'yaml' && <EditorNodePalette editor={editor} />}
      <div className={`editor-center ${editor.mode === 'split' ? 'split-view' : ''}`}>
        {editor.mode !== 'yaml' && <EditorGraph editor={editor} />}
        {editor.mode !== 'visual' && <EditorYamlPreview yaml={editor.yaml} />}
      </div>
      {editor.mode !== 'yaml' && editor.node && <EditorNodeProperties editor={editor} />}
    </div>
  );
}
