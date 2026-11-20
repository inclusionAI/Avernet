import { Button } from '@/components/ui/Button';
import type { EditorNode, EditorSpec } from '@/domain/taskEscortParity/editorTypes';
import { useEffect, useState } from 'react';
import { EditorDialog } from './EditorPrimitives';
import { ExecutorFields, TextField, inputClass } from './inspector/Fields';
import { SettingsDraft } from './inspector/SettingsDraft';
import { parseInspectorJson, validateNode } from './inspector/validation';

export interface EditorInspectorProps {
  node: EditorNode | undefined;
  close: () => void;
  onChange?: (node: EditorNode) => void;
  onDuplicate?: () => void;
  onDelete?: () => void;
  onPendingChange?: (pending: boolean) => void;
  disabled?: boolean;
  nodes?: EditorNode[];
}
function NodeDraft({
  node,
  close,
  onChange,
  onDuplicate,
  onDelete,
  onPendingChange,
  disabled,
  nodes,
}: EditorInspectorProps & { node: EditorNode }) {
  const [draft, setDraft] = useState(node);
  const [raw, setRaw] = useState(JSON.stringify(node, null, 2));
  const [error, setError] = useState('');
  const [applied, setApplied] = useState(JSON.stringify(node, null, 2));
  const [argsPending, setArgsPending] = useState(false);
  const [argsReset, setArgsReset] = useState(0);
  const readonly = disabled || !onChange;
  const dirty = raw !== applied || argsPending;
  useEffect(() => {
    onPendingChange?.(dirty);
  }, [dirty, onPendingChange]);
  useEffect(() => () => onPendingChange?.(false), [onPendingChange]);
  const rawPending = raw !== JSON.stringify(draft, null, 2);
  function update(next: EditorNode) {
    setDraft(next);
    setRaw(JSON.stringify(next, null, 2));
    setError('');
  }
  function apply() {
    if (readonly || argsPending) return;
    try {
      const next = validateNode(node, parseInspectorJson(raw), nodes);
      onChange?.(next);
      setDraft(next);
      setRaw(JSON.stringify(next, null, 2));
      setApplied(JSON.stringify(next, null, 2));
      setError('');
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }
  return (
    <>
      <header className="flex items-center justify-between">
        <h3 className="text-sm font-semibold">节点属性</h3>
        <Button variant="ghost" size="sm" onClick={close} disabled={dirty}>
          关闭节点面板
        </Button>
      </header>
      <p className="break-all font-mono text-xs text-muted-foreground">
        {node.id} · {node.executor.type}
      </p>
      <TextField
        label="节点名称"
        value={draft.title}
        disabled={readonly || rawPending}
        onChange={(title) => update({ ...draft, title })}
      />
      <ExecutorFields
        key={argsReset}
        node={draft}
        disabled={!!readonly || rawPending}
        onChange={update}
        onPendingChange={setArgsPending}
      />
      <p className="text-xs text-muted-foreground">
        依赖：{draft.dependsOn?.join(' → ') || '未配置'}。依赖与结果分支在完整 JSON 中编辑；需要完整节点列表验证。
      </p>
      <details className="rounded-md border border-input p-3">
        <summary className="cursor-pointer text-sm">高级配置 / 完整节点 JSON</summary>
        <p className="text-xs text-muted-foreground">
          保留隐藏字段和原始超时单位；id、类型和坐标只读。不会执行命令或调用工具。
        </p>
        <textarea
          aria-label="完整节点 JSON"
          className={`${inputClass} mt-3 font-mono`}
          rows={18}
          value={raw}
          readOnly={readonly || argsPending}
          onChange={(e) => {
            setRaw(e.target.value);
            setError('');
          }}
        />
      </details>
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
      {dirty && (
        <p role="status" className="text-xs text-muted-foreground">
          有未应用草稿，请应用或放弃后关闭、复制或删除。
        </p>
      )}
      <div className="flex flex-wrap gap-2">
        <Button disabled={readonly || !dirty || argsPending} onClick={apply}>
          应用节点配置
        </Button>
        <Button
          variant="outline"
          disabled={!dirty}
          onClick={() => {
            update(node);
            setArgsPending(false);
            setArgsReset((value) => value + 1);
            setApplied(JSON.stringify(node, null, 2));
          }}
        >
          放弃草稿
        </Button>
      </div>
      <div className="flex gap-2">
        <Button
          variant="outline"
          disabled={readonly || dirty || !onDuplicate}
          onClick={async () => {
            try {
              await onDuplicate?.();
              setError('');
            } catch (cause) {
              setError(cause instanceof Error ? cause.message : String(cause));
            }
          }}
        >
          复制节点
        </Button>
        <Button
          variant="outline"
          disabled={readonly || dirty || !onDelete}
          onClick={async () => {
            try {
              if (window.confirm(`确认删除节点「${node.title || node.id}」？`)) {
                await onDelete?.();
                setError('');
              }
            } catch (cause) {
              setError(cause instanceof Error ? cause.message : String(cause));
            }
          }}
        >
          删除节点
        </Button>
      </div>
      <p className="text-xs text-muted-foreground">
        {readonly ? '只读模式，不会改变工作流。' : '应用仅更新本地工作流草稿，不直接保存或发布。'}
      </p>
    </>
  );
}
export function EditorInspector(props: EditorInspectorProps) {
  return (
    <aside aria-label="节点属性" className="min-w-0 space-y-4 border-l border-input bg-card p-4 text-foreground">
      {props.node ? (
        <NodeDraft key={JSON.stringify(props.node)} {...props} node={props.node} />
      ) : (
        <div className="space-y-3">
          <h3 className="text-sm font-semibold">让每一步都清晰</h3>
          <p className="text-sm text-muted-foreground">选择节点查看配置，编辑这一步的业务配置。</p>
        </div>
      )}
    </aside>
  );
}

export interface EditorSettingsProps {
  spec: EditorSpec;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onChange?: (spec: EditorSpec) => void;
  onPendingChange?: (pending: boolean) => void;
  disabled?: boolean;
}
export function EditorSettings(props: EditorSettingsProps) {
  const [pending, setPending] = useState(false);
  useEffect(() => {
    props.onPendingChange?.(pending);
  }, [pending, props.onPendingChange]);
  useEffect(() => () => props.onPendingChange?.(false), [props.onPendingChange]);
  return (
    <EditorDialog
      title="流程设置"
      open={props.open}
      onOpenChange={(open) => {
        if (open || !pending) props.onOpenChange(open);
      }}
    >
      <SettingsDraft key={`${props.open}:${JSON.stringify(props.spec)}`} {...props} onPendingChange={setPending} />
    </EditorDialog>
  );
}
