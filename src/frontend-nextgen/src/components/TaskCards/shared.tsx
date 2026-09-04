import { Button } from '@/components/ui';
import { Edit3, Lightbulb } from 'lucide-react';
import type { EditableField } from './types';

export function asItems(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : [];
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

export function readTaskCardData(params: Record<string, unknown>) {
  const candidate = params.content ?? params.data ?? params.payload ?? params;
  const source = isRecord(candidate) && isRecord(candidate.renderData) ? candidate.renderData : candidate;
  return isRecord(source) ? source : {};
}

export function ActionButton({
  label,
  onClick,
  variant = 'secondary',
  icon,
}: {
  label: string;
  onClick: () => void;
  variant?: 'default' | 'secondary' | 'destructive';
  icon: React.ReactNode;
}) {
  return (
    <Button size="sm" variant={variant} onClick={onClick} leftIcon={icon} className="flex-1">
      {label}
    </Button>
  );
}

export function FieldList({
  label,
  items,
  fieldName,
  needsConfirmation,
  onEdit,
}: {
  label: string;
  items: string[];
  fieldName: EditableField;
  needsConfirmation: boolean;
  onEdit?: (field: EditableField, value: string | string[]) => void;
}) {
  return (
    <section className="space-y-1.5">
      <div className="flex items-center gap-1.5">
        <span className="text-xs font-medium text-foreground">{label}</span>
        {needsConfirmation ? (
          <span className="inline-flex items-center gap-1 rounded-full bg-warning/10 px-1.5 py-0.5 text-[10px] text-warning">
            <Lightbulb className="h-3 w-3" aria-hidden />
            推断
          </span>
        ) : null}
        {onEdit ? (
          <Button
            variant="ghost"
            size="icon"
            className="ml-auto h-6 w-6"
            aria-label={`编辑${label}`}
            onClick={() => onEdit(fieldName, items)}
          >
            <Edit3 className="h-3.5 w-3.5" aria-hidden />
          </Button>
        ) : null}
      </div>
      {items.length > 0 ? (
        <ul className="ml-4 list-disc space-y-0.5 text-xs leading-5 text-muted-foreground">
          {items.map((item, index) => (
            <li key={`${fieldName}-${index}`}>{item}</li>
          ))}
        </ul>
      ) : (
        <p className="m-0 ml-4 text-xs text-muted-foreground">无</p>
      )}
    </section>
  );
}

export function QuestionList({ questions }: { questions: string[] }) {
  if (questions.length === 0) return null;
  return (
    <section className="space-y-2 rounded-lg bg-primary/5 p-3">
      <div className="flex items-center gap-1.5 text-xs font-medium text-primary">
        <Lightbulb className="h-3.5 w-3.5" aria-hidden />
        需要你确认以下问题
      </div>
      <ol className="m-0 space-y-1.5 pl-5 text-xs leading-5 text-foreground">
        {questions.map((question, index) => (
          <li key={`question-${index}`}>{question}</li>
        ))}
      </ol>
    </section>
  );
}
