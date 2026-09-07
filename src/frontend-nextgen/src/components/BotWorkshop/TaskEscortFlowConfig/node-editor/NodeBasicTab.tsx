import type { TaskEscortWorkflowNode, TaskEscortWorkflowSpec } from '@/components/BotWorkshop/TaskEscort/types';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/Select';
import { Textarea } from '@/components/ui/Textarea';
import { cn } from '@/utils/cn';
import { X } from 'lucide-react';
import { ExecutorFieldRenderer } from './ExecutorFieldRenderer';
import { EXECUTOR_FIELDS } from './NodeFieldUtils';

const EXECUTOR_TYPES = [
  'embedded-agent',
  'action',
  'human',
  'loop-group',
  'collaboration',
  'done',
  'subagent',
  'bcs-route',
  'baas-call',
  'mcp-call',
  'cli-script',
  'subworkflow',
  'approval',
];

interface NodeBasicTabProps {
  node: TaskEscortWorkflowNode;
  spec: TaskEscortWorkflowSpec;
  onChange: (updates: Partial<TaskEscortWorkflowNode>) => void;
}

function toText(value: unknown, fallback = ''): string {
  if (value === null || value === undefined) return fallback;
  if (typeof value === 'string') return value;
  return String(value);
}

function Field({ label, children, className }: { label: string; children: React.ReactNode; className?: string }) {
  return (
    <div className={cn('space-y-1', className)}>
      <label className="block text-[10px] font-medium text-muted-foreground">{label}</label>
      {children}
    </div>
  );
}

export function NodeBasicTab({ node, spec, onChange }: NodeBasicTabProps) {
  const otherNodes = spec.nodes.filter((n) => n.id !== node.id);
  const executor = node.executor ?? { type: 'done' };
  const executorType = typeof executor === 'string' ? executor : toText(executor.type, 'done');
  const executorFields = EXECUTOR_FIELDS[executorType] ?? [];

  const handleExecutorTypeChange = (type: string) => {
    const current = typeof node.executor === 'object' && node.executor ? node.executor : {};
    onChange({ executor: { ...current, type } });
  };

  return (
    <div className="space-y-3">
      <Field label="节点 ID">
        <Input value={node.id} disabled className="bg-muted/50 text-muted-foreground" />
      </Field>

      <Field label="标题">
        <Input value={node.title ?? ''} onChange={(e) => onChange({ title: e.target.value })} placeholder="节点标题" />
      </Field>

      <Field label="执行器类型">
        <Select value={executorType} onValueChange={handleExecutorTypeChange}>
          <SelectTrigger>
            <SelectValue placeholder="选择执行器类型…" />
          </SelectTrigger>
          <SelectContent>
            {EXECUTOR_TYPES.map((type) => (
              <SelectItem key={type} value={type}>
                {type}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </Field>

      <Field label="Phase">
        <Input
          value={node.phase ?? ''}
          onChange={(e) => onChange({ phase: e.target.value || undefined })}
          placeholder="例如：intake, process, finalize"
        />
      </Field>

      <Field label="类型 (type)">
        <Input
          value={node.type ?? ''}
          onChange={(e) => onChange({ type: e.target.value || undefined })}
          placeholder="可选：节点类型"
        />
      </Field>

      <Field label="描述">
        <Textarea
          size="sm"
          value={node.description ?? ''}
          onChange={(e) => onChange({ description: e.target.value || undefined })}
          placeholder="节点用途描述"
          className="min-h-16"
        />
      </Field>

      <Field label="超时时间 (ms)">
        <Input
          type="number"
          value={node.timeoutMs ?? ''}
          onChange={(e) => {
            const value = e.target.value === '' ? undefined : Number(e.target.value);
            onChange({ timeoutMs: value });
          }}
          placeholder="例如：30000"
        />
      </Field>

      <Field label="分支 ID (branchId)">
        <Input
          value={node.branchId ?? ''}
          onChange={(e) => onChange({ branchId: e.target.value || undefined })}
          placeholder="匹配 onResult 分支路由"
        />
      </Field>

      <div>
        <label className="mb-1 block text-[10px] font-medium text-muted-foreground">依赖</label>
        {(node.dependsOn ?? []).length > 0 && (
          <div className="mb-2 flex flex-wrap gap-1">
            {(node.dependsOn ?? []).map((dep) => (
              <span
                key={dep}
                className="inline-flex items-center gap-1 rounded-full bg-primary/10 px-1.5 py-0.5 text-[10px] text-primary"
              >
                {dep}
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  className="h-3.5 w-3.5 text-primary/70 hover:text-destructive"
                  onClick={() => {
                    const next = (node.dependsOn ?? []).filter((d) => d !== dep);
                    onChange({ dependsOn: next.length > 0 ? next : undefined });
                  }}
                >
                  <X className="h-3 w-3" />
                </Button>
              </span>
            ))}
          </div>
        )}
        {otherNodes.length > 0 && (
          <Select
            value="_placeholder"
            onValueChange={(v) => {
              if (v && v !== '_placeholder' && !(node.dependsOn ?? []).includes(v)) {
                onChange({ dependsOn: [...(node.dependsOn ?? []), v] });
              }
            }}
          >
            <SelectTrigger>
              <SelectValue placeholder="添加依赖…" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="_placeholder" className="text-muted-foreground">
                选择节点…
              </SelectItem>
              {otherNodes.map((n) => (
                <SelectItem key={n.id} value={n.id}>
                  {n.title || n.id}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
      </div>

      {executorFields.length > 0 && (
        <div className="border-t border-border pt-2">
          <label className="mb-1 block text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
            执行器配置
          </label>
          <ExecutorFieldRenderer fields={executorFields} executorType={executorType} node={node} onChange={onChange} />
        </div>
      )}
    </div>
  );
}
