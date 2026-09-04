import { Button, Card, CardContent, CardHeader, CardTitle, Textarea } from '@/components/ui';
import { Edit3, Lightbulb, Play, Save, Trash2 } from 'lucide-react';
import { useState } from 'react';
import { submitTaskCardAction } from '@/services/tasks/taskCardBridge';
import { ActionButton, asItems, FieldList } from './shared';
import type { EditableField, TaskCardData, TaskReadyData } from './types';

function EditField({
  value,
  onChange,
  onConfirm,
  onCancel,
}: {
  value: string;
  onChange: (value: string) => void;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <div className="space-y-2">
      <Textarea value={value} onChange={(event) => onChange(event.target.value)} rows={3} autoFocus />
      <div className="flex justify-end gap-2">
        <Button size="sm" variant="ghost" onClick={onCancel}>
          取消
        </Button>
        <Button size="sm" variant="default" onClick={onConfirm}>
          确认
        </Button>
      </div>
    </div>
  );
}

function normalizeTask(data: TaskCardData): TaskReadyData {
  return data.task ?? {
    task_type: 'dynamic',
    goal: data.goal,
    deliverables: data.deliverables,
    acceptance_criteria: data.acceptance_criteria,
    constraints: data.constraints,
    resources: data.resources,
  };
}

export function TaskReadyCard({ data }: { data: TaskCardData }) {
  const task = normalizeTask(data);
  const [editing, setEditing] = useState<EditableField | null>(null);
  const [editValue, setEditValue] = useState('');
  const [submitted, setSubmitted] = useState(false);
  const confirmed = new Set(data.needs_confirmation ?? []);

  const startEdit = (field: EditableField, value: string | string[]) => {
    setEditing(field);
    setEditValue(Array.isArray(value) ? value.join('\n') : value ?? '');
  };
  const cancelEdit = () => {
    setEditing(null);
    setEditValue('');
  };
  const confirmEdit = (label: string) => {
    const lines = editValue
      .split('\n')
      .map((line) => line.trim())
      .filter(Boolean);
    submitTaskCardAction(
      lines.length > 1 ? `修改${label}为：\n${lines.map((line) => `- ${line}`).join('\n')}` : `修改${label}为：${lines[0] ?? ''}`,
    );
    cancelEdit();
  };
  const runAction = (action: 'execute' | 'save' | 'discard') => {
    if (submitted) return;
    setSubmitted(true);
    if (action === 'execute') {
      submitTaskCardAction('执行任务', { __taskAction: 'execute', task });
    } else {
      submitTaskCardAction(action === 'save' ? '暂存任务' : '丢弃任务');
    }
  };
  const fields: Array<[string, EditableField, string[]]> = [
    ['交付物', 'deliverables', asItems(task.deliverables)],
    ['验收标准', 'acceptance_criteria', asItems(task.acceptance_criteria)],
    ['约束', 'constraints', asItems(task.constraints)],
    ['关联资源', 'resources', asItems(task.resources)],
  ];

  return (
    <Card className="w-full max-w-[420px] border-border shadow-sm">
      <CardHeader className="border-b border-border px-4 py-3">
        <CardTitle className="text-sm">任务已就绪</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4 p-4">
        <section className="space-y-1.5">
          <div className="flex items-center gap-1.5">
            <span className="text-xs font-medium text-foreground">目标</span>
            {confirmed.has('goal') ? (
              <span className="inline-flex items-center gap-1 rounded-full bg-warning/10 px-1.5 py-0.5 text-[10px] text-warning">
                <Lightbulb className="h-3 w-3" aria-hidden />
                推断
              </span>
            ) : null}
            <Button
              variant="ghost"
              size="icon"
              className="ml-auto h-6 w-6"
              aria-label="编辑目标"
              onClick={() => startEdit('goal', task.goal ?? '')}
            >
              <Edit3 className="h-3.5 w-3.5" aria-hidden />
            </Button>
          </div>
          {editing === 'goal' ? (
            <EditField value={editValue} onChange={setEditValue} onConfirm={() => confirmEdit('目标')} onCancel={cancelEdit} />
          ) : (
            <p className="m-0 text-sm font-semibold leading-6 text-foreground">{task.goal || '未设定'}</p>
          )}
        </section>
        {fields.map(([label, fieldName, items]) =>
          editing === fieldName ? (
            <EditField
              key={fieldName}
              value={editValue}
              onChange={setEditValue}
              onConfirm={() => confirmEdit(label)}
              onCancel={cancelEdit}
            />
          ) : (
            <FieldList
              key={fieldName}
              label={label}
              fieldName={fieldName}
              items={items}
              needsConfirmation={confirmed.has(fieldName)}
              onEdit={startEdit}
            />
          ),
        )}
        <div className="flex gap-2 pt-1">
          <ActionButton label="丢弃" icon={<Trash2 className="h-3.5 w-3.5" aria-hidden />} variant="destructive" onClick={() => runAction('discard')} />
          <ActionButton label="暂存" icon={<Save className="h-3.5 w-3.5" aria-hidden />} onClick={() => runAction('save')} />
          <ActionButton label="执行" icon={<Play className="h-3.5 w-3.5" aria-hidden />} variant="default" onClick={() => runAction('execute')} />
        </div>
      </CardContent>
    </Card>
  );
}
