import { Button } from '@/components/ui/Button';
import type { WorkflowItem } from '@/services/botWorkshop/agentCodingLegacyService';
import { cn } from '@/utils/cn';
import { ChevronDown, Loader2 } from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { WorkflowDropdownPanel, type WorkflowGroup } from './WorkflowDropdownPanel';

/**
 * WorkflowSelect - 工作流自定义选择组件。
 * 使用自定义下拉框实现，避免 Radix UI Select 锁定页面滚动。
 */
export interface WorkflowSelectProps {
  value: WorkflowItem | null;
  options: WorkflowItem[];
  loading?: boolean;
  disabled?: boolean;
  onChange: (workflow: WorkflowItem | null) => void;
  placeholder?: string;
  className?: string;
}

function getDomainDisplayName(domain: string, domainName?: string): string {
  if (domainName) return domainName;
  if (domain === 'aix') return '官方';
  return domain;
}

function getWorkflowTitle(workflow: WorkflowItem): string {
  return workflow.title || workflow.name;
}

export function WorkflowSelect({
  value,
  options,
  loading = false,
  disabled = false,
  onChange,
  placeholder = '选择此应用的研发工作流',
  className,
}: WorkflowSelectProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [activeDomain, setActiveDomain] = useState<string | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) setIsOpen(false);
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const groupedOptions = useMemo<WorkflowGroup[]>(() => {
    const groupMap = new Map<string, { items: WorkflowItem[]; domainName?: string }>();
    options.forEach((workflow) => {
      const domain = workflow.domain || '其他';
      if (!groupMap.has(domain)) groupMap.set(domain, { items: [], domainName: workflow.domain_name });
      groupMap.get(domain)!.items.push(workflow);
    });

    return Array.from(groupMap.keys())
      .sort((a, b) => {
        if (a === 'aix') return -1;
        if (b === 'aix') return 1;
        return a.localeCompare(b, 'zh-CN');
      })
      .map((domain) => {
        const group = groupMap.get(domain)!;
        return { domain, displayName: getDomainDisplayName(domain, group.domainName), items: group.items };
      });
  }, [options]);

  useEffect(() => {
    if (!isOpen) return;
    setActiveDomain(value?.domain || groupedOptions[0]?.domain || null);
  }, [isOpen, value?.domain, groupedOptions]);

  const handleSelect = useCallback(
    (workflow: WorkflowItem) => {
      onChange(workflow);
      setIsOpen(false);
    },
    [onChange],
  );
  const handleToggle = useCallback(() => {
    if (!disabled && !loading) setIsOpen((previous) => !previous);
  }, [disabled, loading]);
  const selectedTitle = value ? getWorkflowTitle(value) : null;
  const activeGroup = groupedOptions.find((group) => group.domain === activeDomain);

  return (
    <div ref={containerRef} className={cn('relative', className)}>
      <Button
        type="button"
        variant="ghost"
        onClick={handleToggle}
        disabled={disabled || loading}
        className={cn(
          'flex h-auto min-h-0 w-full cursor-pointer items-center justify-between gap-2 rounded-lg border border-slate-200 bg-background px-3 py-1.5 text-left text-[13px] font-normal shadow-none',
          'focus:border-transparent focus:outline-none focus:ring-1 focus:ring-slate-300 disabled:bg-muted disabled:text-slate-400',
          isOpen && 'border-transparent ring-1 ring-slate-300',
        )}
      >
        <span className="truncate">
          {loading ? (
            <span className="flex items-center gap-1.5">
              <Loader2 className="h-3 w-3 animate-spin" />
              加载中...
            </span>
          ) : selectedTitle ? (
            <span>{selectedTitle}</span>
          ) : (
            <span className="text-slate-300">{placeholder}</span>
          )}
        </span>
        <ChevronDown
          size={14}
          className={cn('flex-shrink-0 text-slate-400 transition-transform', isOpen && 'rotate-180')}
        />
      </Button>
      {isOpen && !loading ? (
        <WorkflowDropdownPanel
          options={options}
          groups={groupedOptions}
          activeGroup={activeGroup}
          activeDomain={activeDomain}
          selectedPath={value?.path}
          disabled={disabled}
          onDomainChange={setActiveDomain}
          onSelect={handleSelect}
        />
      ) : null}
    </div>
  );
}

export default WorkflowSelect;
