import {
  CreateBotTooltip,
  CreateBotTooltipContent,
  CreateBotTooltipProvider,
  CreateBotTooltipTrigger,
} from '@/components/BotWorkshop/CreateBotModal/CreateBotTooltip';
import { Button } from '@/components/ui/Button';
import { cn } from '@/utils/cn';
import { AlertTriangle, HelpCircle, KeyRound, Plus, Trash2 } from 'lucide-react';
import React from 'react';
import type { YuqueKbRepoItem } from './types';
import { normalizeYuqueKbRepoItems } from './validators';

export interface YuqueKbReposFieldProps {
  value?: YuqueKbRepoItem[];
  onChange: (next: YuqueKbRepoItem[]) => void;
  disabled?: boolean;
  required?: boolean;
  label?: string;
  placeholder?: string;
  description?: string;
  errors?: Record<number, string>;
  renderError?: (message: string) => React.ReactNode;
  validating?: boolean;
  warning?: string | null;
  tooltipContent?: React.ReactNode;
  tooltipOpen?: boolean;
  onTooltipOpenChange?: (open: boolean) => void;
  className?: string;
}

export const YuqueKbReposField: React.FC<YuqueKbReposFieldProps> = ({
  value,
  onChange,
  disabled = false,
  required = false,
  label = '语雀知识库',
  placeholder = '请输入知识库团队访问地址',
  description,
  errors = {},
  renderError,
  validating = false,
  warning,
  tooltipContent,
  tooltipOpen,
  onTooltipOpenChange,
  className,
}) => {
  const items = normalizeYuqueKbRepoItems(value);
  const displayItems = items.length ? items : [{ url: '', token: '' }];

  const updateAt = (index: number, patch: Partial<YuqueKbRepoItem>) => {
    const next = [...displayItems];
    next[index] = { ...next[index], ...patch };
    onChange(next);
  };

  const removeAt = (index: number) => {
    const next = displayItems.filter((_, itemIndex) => itemIndex !== index);
    onChange(next.length ? next : []);
  };

  const errorRenderer = renderError || ((message: string) => message);

  return (
    <div className={cn('space-y-1.5', className)}>
      <label className="flex items-center gap-1 text-xs font-semibold text-slate-600">
        {label}
        {required ? (
          <span className="ml-0.5 text-red-500">*</span>
        ) : (
          <span className="font-normal text-slate-400">（可选）</span>
        )}
        <span className="font-normal text-slate-400">（可添加多个）</span>
        {tooltipContent && (
          <CreateBotTooltipProvider delayDuration={200}>
            <CreateBotTooltip open={tooltipOpen} onOpenChange={onTooltipOpenChange}>
              <CreateBotTooltipTrigger asChild>
                <HelpCircle className="h-3.5 w-3.5 cursor-help text-slate-400" />
              </CreateBotTooltipTrigger>
              <CreateBotTooltipContent
                side="top"
                className="max-w-[400px]"
                onPointerDownOutside={(event) => {
                  if ((event.target as HTMLElement).closest('video')) {
                    event.preventDefault();
                  }
                }}
              >
                {tooltipContent}
              </CreateBotTooltipContent>
            </CreateBotTooltip>
          </CreateBotTooltipProvider>
        )}
      </label>
      {displayItems.map((repo, index) => (
        <div
          key={index}
          className={cn(
            'space-y-1.5 rounded-lg border p-2.5 transition-colors',
            errors[index] ? 'border-red-300 bg-red-50/30' : 'border-slate-100 bg-white',
          )}
        >
          <div className="flex items-center gap-1.5">
            <input
              type="text"
              value={repo.url || ''}
              onChange={(e) => updateAt(index, { url: e.target.value })}
              placeholder={placeholder}
              disabled={disabled}
              className={cn(
                'flex-1 rounded-lg border px-3 py-1.5 text-sm focus:border-transparent focus:outline-none focus:ring-1 placeholder:text-slate-300 disabled:bg-slate-50 disabled:text-slate-400',
                errors[index] ? 'border-red-300 focus:ring-red-400' : 'border-slate-200 focus:ring-slate-300',
              )}
            />
            {!disabled && displayItems.length > 1 && (
              <Button
                type="button"
                variant="ghost"
                size="icon"
                onClick={() => removeAt(index)}
                aria-label={`删除${label}`}
                className="cursor-pointer text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
              >
                <Trash2 className="h-3.5 w-3.5" />
              </Button>
            )}
          </div>
          <div className="flex items-center gap-1.5">
            <div className="relative flex-1">
              <KeyRound className="pointer-events-none absolute left-3 top-1/2 h-3 w-3 -translate-y-1/2 text-slate-400" />
              <input
                type="text"
                value={repo.token || ''}
                onChange={(e) => updateAt(index, { token: e.target.value })}
                placeholder="空间 Token（必填）"
                disabled={disabled}
                className={cn(
                  'w-full rounded-lg border py-1.5 pl-8 pr-3 text-sm focus:border-transparent focus:outline-none focus:ring-1 placeholder:text-slate-300 disabled:bg-slate-50 disabled:text-slate-400',
                  errors[index] ? 'border-red-300 focus:ring-red-400' : 'border-slate-200 focus:ring-slate-300',
                )}
              />
            </div>
            {!disabled && displayItems.length > 1 && <div className="h-9 w-9 flex-shrink-0" aria-hidden />}
          </div>
          {errors[index] && <p className="pl-1 text-[11px] text-red-500">{errorRenderer(errors[index])}</p>}
        </div>
      ))}
      {description && <p className="text-[11px] leading-relaxed text-slate-400">{description}</p>}
      {validating && (
        <div className="flex items-center gap-2 py-1 text-xs text-slate-400">
          <span className="i-lucide-loader-2 h-3 w-3 animate-spin" />
          正在校验语雀知识库...
        </div>
      )}
      {warning && !validating && (
        <div className="flex items-center gap-1.5 py-1 text-xs text-amber-600">
          <AlertTriangle className="h-3 w-3 flex-shrink-0" />
          {warning}
        </div>
      )}
      {!disabled && (
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={() => onChange([...displayItems, { url: '', token: '' }])}
          disabled={disabled}
          leftIcon={<Plus className="h-3 w-3" />}
          className="cursor-pointer px-0 text-xs hover:bg-transparent"
        >
          添加
        </Button>
      )}
    </div>
  );
};

export default YuqueKbReposField;
