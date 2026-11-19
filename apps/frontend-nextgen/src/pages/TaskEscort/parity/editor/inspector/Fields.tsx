import { Button } from '@/components/ui/Button';
import type { EditorNode } from '@/domain/taskEscortParity/editorTypes';
import { useEffect, useId, useState } from 'react';
import { parseInspectorJson } from './validation';
export const inputClass =
  'w-full rounded-md border border-input bg-background px-3 py-2 text-sm text-foreground disabled:opacity-50';
export function TextField({
  label,
  value,
  disabled,
  onChange,
  multiline = false,
}: {
  label: string;
  value: unknown;
  disabled?: boolean;
  onChange: (value: string) => void;
  multiline?: boolean;
}) {
  const id = useId();
  const compatible = value === undefined || typeof value === 'string';
  const props = {
    id,
    className: inputClass,
    value: typeof value === 'string' ? value : value === undefined ? '' : JSON.stringify(value),
    readOnly: disabled || !compatible,
    onChange: (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => onChange(e.target.value),
  };
  return (
    <div className="space-y-2">
      <label htmlFor={id} className="text-sm font-medium">
        {label}
      </label>
      {multiline ? <textarea {...props} rows={5} /> : <input {...props} />}
      {!compatible && <p className="text-xs text-muted-foreground">复杂原值保留；请使用完整 JSON。</p>}
    </div>
  );
}
const fields: Record<string, Array<[string, string]>> = {
  'embedded-agent': [
    ['skillName', '技能名称'],
    ['prompt', '任务指令'],
  ],
  subagent: [
    ['skillName', '技能名称'],
    ['prompt', '任务指令'],
  ],
  'cli-script': [['command', '命令或脚本路径']],
  'mcp-call': [
    ['server', '工具服务'],
    ['tool', '工具名称'],
  ],
  human: [['prompt', '希望用户做什么']],
  approval: [
    ['message', '审批内容'],
    ['skillName', '审批技能'],
  ],
  done: [['message', '完成文案']],
};
export function ArgsField({
  node,
  disabled,
  onChange,
  onPendingChange,
}: {
  node: EditorNode;
  disabled: boolean;
  onChange: (node: EditorNode) => void;
  onPendingChange?: (pending: boolean) => void;
}) {
  const [text, setText] = useState(JSON.stringify(node.executor.args ?? null, null, 2));
  const [error, setError] = useState('');
  const baseline = JSON.stringify(node.executor.args ?? null, null, 2);
  const pending = text !== baseline;
  useEffect(() => {
    onPendingChange?.(pending);
  }, [pending, onPendingChange]);
  useEffect(() => () => onPendingChange?.(false), [onPendingChange]);
  return (
    <div className="space-y-2">
      <label className="text-sm">
        参数 JSON
        <textarea
          aria-label="参数 JSON"
          className={inputClass}
          value={text}
          readOnly={disabled}
          onChange={(e) => setText(e.target.value)}
        />
      </label>
      <Button
        variant="outline"
        disabled={disabled}
        onClick={() => {
          try {
            const args = parseInspectorJson(text);
            onChange({ ...node, executor: { ...node.executor, args } });
            setText(JSON.stringify(args, null, 2));
            setError('');
          } catch (cause) {
            setError(cause instanceof Error ? cause.message : String(cause));
          }
        }}
      >
        更新参数草稿
      </Button>
      <Button
        variant="outline"
        disabled={!pending}
        onClick={() => {
          setText(baseline);
          setError('');
        }}
      >
        放弃参数草稿
      </Button>
      {pending && (
        <p role="status" className="text-xs text-muted-foreground">
          参数尚未更新，请更新或放弃参数草稿。
        </p>
      )}
      {error && (
        <p role="alert" className="text-destructive">
          {error}
        </p>
      )}
      <p className="text-xs text-muted-foreground">保留参数数组、对象及空格。更新后仍需应用节点配置。</p>
    </div>
  );
}

export function ExecutorFields({
  node,
  disabled,
  onChange,
  onPendingChange,
}: {
  node: EditorNode;
  disabled: boolean;
  onChange: (node: EditorNode) => void;
  onPendingChange?: (pending: boolean) => void;
}) {
  return (
    <section className="space-y-3">
      <h4 className="text-xs font-semibold">执行器 · {node.executor.type}</h4>
      {(fields[node.executor.type] ?? []).map(([key, label]) => (
        <TextField
          key={key}
          label={label}
          value={node.executor[key]}
          disabled={disabled}
          multiline={['prompt', 'message'].includes(key)}
          onChange={(value) => onChange({ ...node, executor: { ...node.executor, [key]: value } })}
        />
      ))}
      {['cli-script', 'mcp-call'].includes(node.executor.type) && (
        <ArgsField node={node} disabled={disabled} onChange={onChange} onPendingChange={onPendingChange} />
      )}
      {!fields[node.executor.type] && (
        <p className="text-xs text-muted-foreground">兼容配置模式；保留真实类型，在完整节点 JSON 中编辑。</p>
      )}
    </section>
  );
}
