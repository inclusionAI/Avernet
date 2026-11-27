import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import {
  fetchArchitectDomainOptions,
  type ArchitectDomainOption,
} from '@/services/botWorkshop/agentCodingLegacyService';
import { ChevronDown, ChevronRight } from 'lucide-react';
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';

export const ARCHITECT_NAME_FIELD_KEY = 'architect_name';

function getArchitectOptionSearchText(option: ArchitectDomainOption): string {
  return option.label.toLowerCase();
}

function filterArchitectOptionTree(options: ArchitectDomainOption[], keyword: string): ArchitectDomainOption[] {
  const normalizedKeyword = keyword.trim().toLowerCase();
  if (!normalizedKeyword) return options;

  return options.flatMap((option) => {
    const originalChildren = option.children || [];
    const filteredChildren = originalChildren.length
      ? filterArchitectOptionTree(originalChildren, normalizedKeyword)
      : [];
    const selfMatched = getArchitectOptionSearchText(option).includes(normalizedKeyword);

    if (!selfMatched && filteredChildren.length === 0) return [];
    return [
      {
        ...option,
        ...(selfMatched && originalChildren.length
          ? { children: originalChildren }
          : filteredChildren.length
          ? { children: filteredChildren }
          : { children: undefined }),
      },
    ];
  });
}

function countSelectableArchitectOptions(options: ArchitectDomainOption[]): number {
  return options.reduce((total, option) => {
    if (option.children?.length) {
      return total + countSelectableArchitectOptions(option.children);
    }
    return total + 1;
  }, 0);
}

function getArchitectOptionKey(option: ArchitectDomainOption, depth: number, parentKey = ''): string {
  return `${parentKey}/${depth}-${option.value}-${option.code || ''}`;
}

export interface ArchitectNameFieldProps {
  label: string;
  value: any;
  disabled?: boolean;
  required: boolean;
  placeholder?: string;
  description?: string;
  onChange: (next: string) => void;
}

export function ArchitectNameField({
  label,
  value,
  disabled,
  required,
  placeholder,
  description,
  onChange,
}: ArchitectNameFieldProps) {
  const [options, setOptions] = useState<ArchitectDomainOption[]>([]);
  const [loading, setLoading] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [open, setOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expandedKeys, setExpandedKeys] = useState<Set<string>>(() => new Set());
  const requestedRef = useRef(false);
  const inputValue = typeof value === 'string' ? value : '';

  const loadOptions = useCallback(async () => {
    if (requestedRef.current || disabled) return;
    requestedRef.current = true;
    setLoading(true);
    setError(null);
    try {
      const next = await fetchArchitectDomainOptions();
      setOptions(next);
      setExpandedKeys(new Set());
      setLoaded(true);
    } catch (err: any) {
      requestedRef.current = false;
      console.error('[ArchitectNameField] 查询架构域失败:', err);
      setError(err?.data?.message || err?.message || '架构师名称列表查询失败');
    } finally {
      setLoading(false);
    }
  }, [disabled]);

  useEffect(() => {
    loadOptions();
  }, [loadOptions]);

  const isSearching = inputValue.trim().length > 0;
  const matchedOptions = useMemo(() => filterArchitectOptionTree(options, inputValue), [inputValue, options]);
  const matchedOptionCount = useMemo(() => countSelectableArchitectOptions(matchedOptions), [matchedOptions]);
  const shouldShowDropdown = open && !disabled && (loading || error || loaded);

  const toggleExpanded = useCallback((key: string) => {
    setExpandedKeys((prev) => {
      const next = new Set(prev);
      if (next.has(key)) {
        next.delete(key);
      } else {
        next.add(key);
      }
      return next;
    });
  }, []);

  function renderOptionTree(treeOptions: ArchitectDomainOption[], depth = 0, parentKey = ''): React.ReactNode {
    return treeOptions.map((option) => {
      const optionKey = getArchitectOptionKey(option, depth, parentKey);
      const hasChildren = !!option.children?.length;
      const expanded = isSearching || expandedKeys.has(optionKey);
      const selected = !hasChildren && inputValue === option.value;

      return (
        <React.Fragment key={optionKey}>
          <Button
            variant="ghost"
            size="sm"
            className={`group h-auto w-full cursor-pointer justify-start rounded-none py-2 pr-3 text-left text-xs transition-colors ${
              selected ? 'bg-primary/10 text-primary' : 'hover:bg-muted'
            }`}
            style={{ paddingLeft: 10 + depth * 20 }}
            onMouseDown={(event) => event.preventDefault()}
            onClick={() => {
              if (hasChildren) {
                if (!isSearching) {
                  toggleExpanded(optionKey);
                }
                return;
              }
              onChange(option.value);
              setOpen(false);
            }}
          >
            <span className="flex min-w-0 w-full items-start gap-2">
              <span className="mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center text-muted-foreground group-hover:text-primary">
                {hasChildren ? (
                  expanded ? (
                    <ChevronDown size={14} strokeWidth={1.8} />
                  ) : (
                    <ChevronRight size={14} strokeWidth={1.8} />
                  )
                ) : null}
              </span>
              <span className="block min-w-0 flex-1">
                <span
                  className={`block truncate ${
                    hasChildren ? 'font-medium text-foreground' : 'font-normal text-foreground'
                  }`}
                >
                  {option.label}
                </span>
                {option.ownerName && (
                  <span className="mt-0.5 block truncate text-[11px] text-muted-foreground">{option.ownerName}</span>
                )}
              </span>
              {hasChildren && !isSearching && (
                <span className="mt-0.5 shrink-0 text-[10px] text-muted-foreground/70">{option.children?.length}</span>
              )}
            </span>
          </Button>
          {hasChildren && expanded ? renderOptionTree(option.children || [], depth + 1, optionKey) : null}
        </React.Fragment>
      );
    });
  }

  return (
    /* figma 稿 node 2017:18988:架构域名称 = 搜索下拉,展开面板选中项高亮;placeholder/口径沿用。 */
    <div className="relative space-y-1.5">
      <label className="flex items-center gap-1 text-xs font-semibold text-foreground">
        {label}
        {required ? (
          <span className="ml-0.5 text-destructive">*</span>
        ) : (
          <span className="font-normal text-muted-foreground">（可选）</span>
        )}
      </label>
      <div className="relative">
        <Input
          type="text"
          value={inputValue}
          disabled={disabled}
          placeholder={loading ? '加载中...' : placeholder || '请输入或选择架构师名称'}
          onFocus={() => {
            setOpen(true);
            loadOptions();
          }}
          onChange={(e) => {
            setOpen(true);
            onChange(e.target.value);
          }}
          onBlur={() => {
            window.setTimeout(() => setOpen(false), 120);
          }}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && inputValue.trim()) {
              event.preventDefault();
              setOpen(false);
              event.currentTarget.blur();
            }
          }}
          className="h-8 w-full rounded-lg border border-input bg-background px-3 py-1.5 pr-8 text-sm text-foreground outline-none transition-shadow placeholder:text-muted-foreground/50 focus:border-transparent focus:ring-1 focus:ring-muted-foreground/15 focus:ring-offset-0 focus-visible:ring-1 focus-visible:ring-muted-foreground/15 focus-visible:ring-offset-0 disabled:bg-muted/60 disabled:text-muted-foreground"
        />
        {loading && (
          <div className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2">
            <span className="h-3 w-3 animate-spin rounded-full border-2 border-muted border-t-muted-foreground" />
          </div>
        )}
      </div>
      {shouldShowDropdown && (
        <div className="absolute left-0 right-0 top-full z-50 mt-1 overflow-hidden rounded-xl border border-border bg-card shadow-xl">
          {!loading && !error && loaded && isSearching && (
            <div className="border-b border-divider bg-muted/80 px-3 py-2 text-[11px] text-muted-foreground">
              {`匹配到 ${matchedOptionCount} 项`}
            </div>
          )}
          <div className="max-h-72 overflow-auto py-1">
            {loading ? (
              <div className="px-3 py-2 text-xs text-muted-foreground">加载中...</div>
            ) : error ? (
              <div className="px-3 py-2 text-xs text-destructive">{error}</div>
            ) : matchedOptionCount > 0 ? (
              renderOptionTree(matchedOptions)
            ) : (
              <div className="px-3 py-2 text-xs text-muted-foreground">未找到匹配项，可直接输入新名称</div>
            )}
          </div>
        </div>
      )}
      {description && <p className="text-[11px] leading-relaxed text-muted-foreground">{description}</p>}
    </div>
  );
}

export default ArchitectNameField;
