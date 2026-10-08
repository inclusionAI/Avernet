import type { TaskEscortWorkflowNode } from '@/components/BotWorkshop/TaskEscort/types';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/Select';
import { Textarea } from '@/components/ui/Textarea';
import { cn } from '@/utils/cn';
import { getExecutorFieldValue, handleExecutorFieldChange, type FieldDef } from './NodeFieldUtils';

interface ExecutorFieldRendererProps {
  fields: FieldDef[];
  executorType: string;
  node: TaskEscortWorkflowNode;
  onChange: (updates: Partial<TaskEscortWorkflowNode>) => void;
}

const inputClass =
  'w-full rounded-md border border-input bg-background px-2 py-1 text-xs text-foreground focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary';

export function ExecutorFieldRenderer({ fields, executorType, node, onChange }: ExecutorFieldRendererProps) {
  return (
    <div className="space-y-1.5">
      {fields.map((field) => {
        // baas-call: botId 仅在 mode=message 时显示
        if (
          executorType === 'baas-call' &&
          field.key === 'executor.botId' &&
          typeof node.executor === 'object' &&
          node.executor?.mode !== 'message'
        ) {
          return null;
        }

        return (
          <div key={field.key}>
            <label className="mb-0.5 block text-[10px] font-medium text-muted-foreground">
              {field.label}
              {field.description && <span className="ml-1 text-muted-foreground/70">({field.description})</span>}
            </label>

            {field.type === 'textarea' || field.type === 'json' ? (
              <Textarea
                size="sm"
                value={getExecutorFieldValue(field.key, node)}
                onChange={(e) => handleExecutorFieldChange(field.key, e.target.value, node, onChange)}
                placeholder={field.placeholder ?? (field.type === 'json' ? '{"key": "value"}' : '')}
                className={cn(inputClass, 'font-mono')}
              />
            ) : field.type === 'select' ? (
              <Select
                value={getExecutorFieldValue(field.key, node) || '_empty'}
                onValueChange={(v) => handleExecutorFieldChange(field.key, v === '_empty' ? '' : v, node, onChange)}
              >
                <SelectTrigger className="h-7 text-xs">
                  <SelectValue placeholder="—" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="_empty" className="text-xs text-muted-foreground">
                    —
                  </SelectItem>
                  {field.options?.map((opt) => (
                    <SelectItem key={opt} value={opt} className="text-xs">
                      {opt}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            ) : (
              <input
                type={field.type}
                value={getExecutorFieldValue(field.key, node)}
                onChange={(e) => handleExecutorFieldChange(field.key, e.target.value, node, onChange)}
                placeholder={field.placeholder}
                className={inputClass}
              />
            )}
          </div>
        );
      })}
    </div>
  );
}
