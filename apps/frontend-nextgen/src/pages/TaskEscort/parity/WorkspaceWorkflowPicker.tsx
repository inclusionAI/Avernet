import { Button } from '@/components/ui/Button';
import { Empty } from '@/components/ui/Empty';
import { Input } from '@/components/ui/Input';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/Popover';
import type { TaskEscortWorkflowSummary } from '@/domain/taskEscortParity/types';
import { Check, ChevronDown } from 'lucide-react';
import { useState } from 'react';

export function WorkspaceWorkflowPicker({
  workflows,
  selected,
  onSelect,
  loading,
}: {
  workflows: TaskEscortWorkflowSummary[];
  selected: TaskEscortWorkflowSummary | null;
  onSelect: (id: string) => void;
  loading?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState('');
  const query = search.trim().toLowerCase();
  const visible = workflows.filter(
    (row) => !query || row.id.toLowerCase().includes(query) || row.title.toLowerCase().includes(query),
  );
  return (
    <Popover
      open={open}
      onOpenChange={(value) => {
        setOpen(value);
        if (!value) setSearch('');
      }}
    >
      <PopoverTrigger asChild>
        <Button
          variant="ghost"
          role="combobox"
          aria-label={`切换工作流，当前：${selected?.title ?? '选择工作流'}`}
          aria-expanded={open}
          disabled={loading || !workflows.length}
          className="workspace-workflow-picker"
        >
          <span className="truncate">{selected?.title ?? '选择工作流'}</span>
          <ChevronDown size={14} aria-hidden />
        </Button>
      </PopoverTrigger>
      <PopoverContent
        align="start"
        role="dialog"
        aria-label="选择工作流"
        className="w-[22rem] max-w-[calc(100vw-2rem)] p-0"
      >
        <div className="border-b border-border p-3">
          <p className="text-xs font-semibold">切换工作流</p>
          <p className="mt-1 text-xs text-muted-foreground">选择后保留当前功能视图</p>
          <Input
            aria-label="搜索工作流"
            placeholder="搜索工作流…"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            className="mt-3"
          />
        </div>
        <div className="max-h-64 overflow-y-auto p-1.5">
          {visible.length ? (
            visible.map((row) => (
              <Button
                key={row.id}
                variant="ghost"
                aria-pressed={row.id === selected?.id}
                className={`h-auto min-h-12 w-full justify-start text-left ${
                  row.id === selected?.id ? 'bg-primary/10 text-primary' : ''
                }`}
                onClick={() => {
                  onSelect(row.id);
                  setOpen(false);
                  setSearch('');
                }}
              >
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-xs font-medium">{row.title}</span>
                  <span className="mt-1 block truncate font-mono text-xs text-muted-foreground">{row.id}</span>
                </span>
                {row.lastStatus && <span className="text-xs text-muted-foreground">{row.lastStatus}</span>}
                {row.id === selected?.id && <Check size={14} aria-hidden />}
              </Button>
            ))
          ) : (
            <Empty title="无匹配工作流" description={search ? '换个关键词试试' : '暂无可访问的工作流'} />
          )}
        </div>
      </PopoverContent>
    </Popover>
  );
}
