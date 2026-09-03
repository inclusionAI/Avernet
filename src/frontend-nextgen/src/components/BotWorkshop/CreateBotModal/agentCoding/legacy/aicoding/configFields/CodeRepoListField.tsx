import AntCodeProjectSelect from '@/components/BotWorkshop/CreateBotModal/agentCoding/legacy/appcoding/AntCodeProjectSelect';
import { Button } from '@/components/ui/Button';
import { cn } from '@/utils/cn';
import { Plus, Trash2 } from 'lucide-react';
import React from 'react';
import type { CodeRepoItem } from './types';
import { normalizeCodeRepoItems } from './validators';

export interface CodeRepoListFieldProps {
  value?: CodeRepoItem[];
  onChange: (next: CodeRepoItem[]) => void;
  disabled?: boolean;
  removeDisabled?: boolean;
  required?: boolean;
  label?: string;
  placeholder?: string;
  description?: string;
  allowMultiple?: boolean;
  duplicateUrls?: Set<string>;
  className?: string;
}

export const CodeRepoListField: React.FC<CodeRepoListFieldProps> = ({
  value,
  onChange,
  disabled = false,
  removeDisabled = false,
  required = false,
  label,
  placeholder = '输入项目名称搜索...',
  description,
  allowMultiple = true,
  duplicateUrls,
  className,
}) => {
  const items = normalizeCodeRepoItems(value);
  const displayItems = items.length ? items : [{ repo_url: '' }];

  const updateAt = (index: number, repoUrl: string) => {
    const next = [...displayItems];
    next[index] = { ...next[index], repo_url: repoUrl };
    onChange(next);
  };

  const removeAt = (index: number) => {
    const next = displayItems.filter((_, itemIndex) => itemIndex !== index);
    onChange(next.length ? next : []);
  };

  return (
    <div className={cn('space-y-2', className)}>
      {label && (
        <label className="block text-xs font-semibold text-slate-600">
          {label}
          {required ? (
            <span className="ml-0.5 text-red-500">*</span>
          ) : (
            <span className="ml-1 font-normal text-slate-400">（可选）</span>
          )}
          {allowMultiple && <span className="ml-1 font-normal text-slate-400">（可添加多个）</span>}
        </label>
      )}
      {displayItems.map((repo, index) => {
        const repoUrl = repo.repo_url || '';
        const hasDuplicate = !!(repoUrl.trim() && duplicateUrls?.has(repoUrl.trim()));
        return (
          <div key={index} className="flex items-center gap-1.5">
            <AntCodeProjectSelect
              value={repoUrl}
              onChange={(url) => updateAt(index, url)}
              disabled={disabled}
              placeholder={placeholder}
              className="min-w-0 flex-1"
              error={hasDuplicate}
            />
            {allowMultiple && !removeDisabled && displayItems.length > 1 && (
              <Button
                type="button"
                variant="ghost"
                size="icon"
                onClick={() => removeAt(index)}
                disabled={disabled}
                aria-label={`删除${label || '代码仓库'}`}
                className="cursor-pointer text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
              >
                <Trash2 className="h-3.5 w-3.5" />
              </Button>
            )}
          </div>
        );
      })}
      {description && <p className="text-[11px] leading-relaxed text-slate-400">{description}</p>}
      {allowMultiple && !removeDisabled && (
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={() => onChange([...displayItems, { repo_url: '' }])}
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

export default CodeRepoListField;
