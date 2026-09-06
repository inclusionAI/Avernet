import type { TaskEscortWorkflowNode } from '@/components/BotWorkshop/TaskEscort/types';
import { Input } from '@/components/ui/Input';
import { Textarea } from '@/components/ui/Textarea';

interface JsonTextAreaProps {
  value: Record<string, unknown> | undefined;
  onChange: (value: Record<string, unknown> | undefined) => void;
  placeholder: string;
}

function JsonTextArea({ value, onChange, placeholder }: JsonTextAreaProps) {
  const text = value ? JSON.stringify(value, null, 2) : '';
  return (
    <Textarea
      size="sm"
      value={text}
      onChange={(e) => {
        const raw = e.target.value.trim();
        if (!raw) {
          onChange(undefined);
          return;
        }
        try {
          const parsed = JSON.parse(raw);
          if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
            onChange(parsed as Record<string, unknown>);
          }
        } catch {
          // 保留上一次的合法值，避免编辑过程中清空
        }
      }}
      placeholder={placeholder}
      className="min-h-24 font-mono text-xs"
    />
  );
}

interface NodeAdvancedTabProps {
  node: TaskEscortWorkflowNode;
  onChange: (updates: Partial<TaskEscortWorkflowNode>) => void;
}

export function NodeAdvancedTab({ node, onChange }: NodeAdvancedTabProps) {
  const retry = node.retry ?? {};

  return (
    <div className="space-y-3">
      <div className="space-y-1">
        <label className="block text-[10px] font-medium text-muted-foreground">重试次数</label>
        <Input
          type="number"
          value={retry.maxAttempts ?? ''}
          onChange={(e) => {
            const value = e.target.value === '' ? undefined : Number(e.target.value);
            onChange({ retry: { ...retry, maxAttempts: value } });
          }}
          placeholder="例如：3"
        />
      </div>

      <div className="space-y-1">
        <label className="block text-[10px] font-medium text-muted-foreground">重试间隔 (ms)</label>
        <Input
          type="number"
          value={retry.delayMs ?? ''}
          onChange={(e) => {
            const value = e.target.value === '' ? undefined : Number(e.target.value);
            onChange({ retry: { ...retry, delayMs: value } });
          }}
          placeholder="例如：1000"
        />
      </div>

      <div className="space-y-1">
        <label className="block text-[10px] font-medium text-muted-foreground">退避策略</label>
        <Input
          value={retry.backoff ?? ''}
          onChange={(e) => {
            const value = e.target.value || undefined;
            onChange({ retry: { ...retry, backoff: value } });
          }}
          placeholder="例如：fixed / exponential"
        />
      </div>

      <div className="space-y-1">
        <label className="block text-[10px] font-medium text-muted-foreground">Input (JSON)</label>
        <JsonTextArea value={node.input} onChange={(input) => onChange({ input })} placeholder='{"key": "value"}' />
      </div>

      <div className="space-y-1">
        <label className="block text-[10px] font-medium text-muted-foreground">Output (JSON)</label>
        <JsonTextArea value={node.output} onChange={(output) => onChange({ output })} placeholder='{"key": "value"}' />
      </div>

      <div className="space-y-1">
        <label className="block text-[10px] font-medium text-muted-foreground">Config (JSON)</label>
        <JsonTextArea value={node.config} onChange={(config) => onChange({ config })} placeholder='{"key": "value"}' />
      </div>
    </div>
  );
}
