import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/Select';
import { Textarea } from '@/components/ui/Textarea';
import type { BotTemplateField } from '@/services/botWorkshop/agentCodingTemplateService';
import { cn } from '@/utils/cn';
import React from 'react';

interface DynamicTemplateFieldControlProps {
  field: BotTemplateField;
  value: any;
  options: { label: string; value: string }[];
  required: boolean;
  disabled?: boolean;
  onChange: (value: any) => void;
}

function fieldTypeOf(field: BotTemplateField): string {
  return String(field.field_type ?? field.type ?? 'string').toLowerCase();
}

export function DynamicTemplateFieldControl({
  field,
  value,
  options,
  required,
  disabled,
  onChange,
}: DynamicTemplateFieldControlProps) {
  const type = fieldTypeOf(field);
  const commonClass =
    'w-full rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs text-slate-700 outline-none transition-shadow placeholder:text-slate-300 focus:border-transparent focus:ring-2 focus:ring-slate-300 disabled:bg-slate-50 disabled:text-slate-400';
  const update = (next: any) => onChange(next);

  let control: React.ReactNode;
  switch (type) {
    case 'boolean':
    case 'checkbox':
    case 'switch':
      control = (
        <label className="inline-flex items-center gap-2 text-xs text-slate-600">
          <input
            type="checkbox"
            checked={!!value}
            disabled={disabled}
            onChange={(event) => update(event.target.checked)}
            className="h-3.5 w-3.5 rounded border-slate-300 text-lavender-600"
          />
          启用
        </label>
      );
      break;
    case 'select':
    case 'enum':
      control = (
        <Select value={value ? String(value) : undefined} disabled={disabled} onValueChange={update}>
          <SelectTrigger className={cn(commonClass, disabled ? 'cursor-not-allowed' : 'cursor-pointer')}>
            <SelectValue placeholder="请选择" />
          </SelectTrigger>
          <SelectContent>
            {options.map((option) => (
              <SelectItem key={option.value} value={option.value}>
                {option.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      );
      break;
    case 'multi_select':
      control = (
        <div
          role="listbox"
          aria-label={field.field_name || field.field_key}
          aria-multiselectable="true"
          className={cn(
            commonClass,
            'flex min-h-[72px] flex-wrap items-start gap-1.5 py-2',
            disabled ? 'cursor-not-allowed opacity-60' : 'cursor-pointer',
          )}
        >
          {options.map((option) => {
            const current = (Array.isArray(value) ? value : []).map(String);
            const selected = current.includes(option.value);
            return (
              <Button
                key={option.value}
                type="button"
                variant={selected ? 'secondary' : 'ghost'}
                size="sm"
                role="option"
                aria-selected={selected}
                disabled={disabled}
                onClick={() =>
                  update(selected ? current.filter((item) => item !== option.value) : [...current, option.value])
                }
              >
                {option.label}
              </Button>
            );
          })}
        </div>
      );
      break;
    case 'string_array':
      control = (
        <Textarea
          value={(Array.isArray(value) ? value : []).join('\n')}
          disabled={disabled}
          rows={3}
          placeholder={field.placeholder || '每行填写一个值'}
          onChange={(event) =>
            update(
              event.target.value
                .split('\n')
                .map((item) => item.trim())
                .filter(Boolean),
            )
          }
          className={commonClass}
        />
      );
      break;
    case 'object_array':
      control = (
        <Textarea
          value={typeof value === 'string' ? value : JSON.stringify(value || [], null, 2)}
          disabled={disabled}
          rows={4}
          placeholder={field.placeholder || '请输入 JSON 数组'}
          onChange={(event) => {
            try {
              update(JSON.parse(event.target.value || '[]'));
            } catch {
              update(event.target.value);
            }
          }}
          className={commonClass}
        />
      );
      break;
    case 'textarea':
    case 'text_area':
    case 'markdown':
      control = (
        <Textarea
          value={String(value ?? '')}
          disabled={disabled}
          rows={4}
          placeholder={field.placeholder}
          onChange={(event) => update(event.target.value)}
          className={commonClass}
        />
      );
      break;
    case 'number':
      control = (
        <Input
          type="number"
          value={value ?? ''}
          disabled={disabled}
          placeholder={field.placeholder}
          onChange={(event) => update(event.target.value === '' ? '' : Number(event.target.value))}
          className={commonClass}
        />
      );
      break;
    case 'password':
      control = (
        <Input
          type="password"
          value={String(value ?? '')}
          disabled={disabled}
          placeholder={field.placeholder}
          onChange={(event) => update(event.target.value)}
          className={commonClass}
        />
      );
      break;
    default:
      control = (
        <Input
          type="text"
          value={String(value ?? '')}
          disabled={disabled}
          placeholder={field.placeholder}
          onChange={(event) => update(event.target.value)}
          className={commonClass}
        />
      );
  }

  return (
    <div className="space-y-1">
      <label className="text-xs font-semibold text-slate-600">
        {field.field_name || field.field_key}
        {required ? <span className="ml-0.5 text-red-500">*</span> : null}
      </label>
      {control}
      {field.description ? <p className="text-[11px] leading-relaxed text-slate-400">{field.description}</p> : null}
    </div>
  );
}
