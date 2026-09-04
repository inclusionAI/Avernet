import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui';
import { Lightbulb } from 'lucide-react';
import { asItems, FieldList, QuestionList } from './shared';
import type { EditableField, TaskCardData } from './types';

export function TaskClarifyCard({ data }: { data: TaskCardData }) {
  const missing = new Set(data.missing_fields ?? []);
  const confirmed = new Set(data.needs_confirmation ?? []);
  const fields: Array<[string, EditableField, string[]]> = [
    ['交付物', 'deliverables', asItems(data.deliverables)],
    ['验收标准', 'acceptance_criteria', asItems(data.acceptance_criteria)],
    ['约束', 'constraints', asItems(data.constraints)],
    ['关联资源', 'resources', asItems(data.resources)],
  ];

  return (
    <Card className="w-full max-w-[420px] border-border shadow-sm">
      <CardHeader className="border-b border-border px-4 py-3">
        <CardTitle className="text-sm">任务澄清</CardTitle>
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
          </div>
          <p className="m-0 text-sm font-semibold leading-6 text-foreground">{data.goal || '未设定'}</p>
        </section>
        {fields.map(([label, fieldName, items]) => (
          <div key={fieldName}>
            {missing.has(fieldName) ? (
              <>
                <p className="m-0 text-xs font-medium text-foreground">{label}</p>
                <p className="m-0 ml-4 text-xs text-muted-foreground">待补充</p>
              </>
            ) : (
              <FieldList label={label} fieldName={fieldName} items={items} needsConfirmation={confirmed.has(fieldName)} />
            )}
          </div>
        ))}
        <QuestionList questions={asItems(data.questions)} />
        <p className="m-0 text-[11px] text-muted-foreground">
          {data.questions?.length ? '请在对话中回答以上问题' : '直接在对话中补充缺失信息即可'}
        </p>
      </CardContent>
    </Card>
  );
}
