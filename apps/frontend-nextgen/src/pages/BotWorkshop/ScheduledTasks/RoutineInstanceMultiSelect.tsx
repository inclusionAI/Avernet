import { Checkbox } from '@/components/ui/Checkbox';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/Popover';
import { cn } from '@/utils/cn';
import { ChevronDown } from 'lucide-react';

/** 多实例环境下的运行实例多选：用于创建定时任务时勾选多个目标实例，逐实例提交创建请求。 */
export function RoutineInstanceMultiSelect({
  value,
  options,
  disabled,
  onChange,
}: {
  value: string[];
  options: string[];
  disabled?: boolean;
  onChange: (next: string[]) => void;
}) {
  const toggle = (id: string, checked: boolean) =>
    onChange(checked ? [...value, id] : value.filter((item) => item !== id));
  return (
    <Popover>
      <PopoverTrigger
        aria-label="运行实例"
        disabled={disabled}
        className={cn(
          'flex h-9 w-full items-center justify-between gap-2 rounded-md border border-input bg-background px-3 py-2 text-xs text-foreground outline-none transition-colors',
          'placeholder:text-muted-foreground hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2',
          'cursor-pointer disabled:cursor-not-allowed disabled:opacity-50 data-[placeholder]:text-muted-foreground [&>span]:truncate',
        )}
      >
        {value.length > 0 ? (
          <span className="truncate">已选 {value.length} 个实例</span>
        ) : (
          <span className="truncate text-muted-foreground">请选择运行实例</span>
        )}
        <ChevronDown aria-hidden className="size-4 shrink-0 opacity-50" />
      </PopoverTrigger>
      <PopoverContent align="start" className="w-80 p-1">
        <div className="max-h-64 space-y-0.5 overflow-y-auto">
          {options.map((id) => (
            <label
              key={id}
              className="flex cursor-pointer items-center gap-2 rounded-sm px-2 py-1.5 text-xs hover:bg-accent"
            >
              <Checkbox
                checked={value.includes(id)}
                onCheckedChange={(checked) => toggle(id, checked)}
                aria-label={id}
              />
              <span className="truncate font-mono">{id}</span>
            </label>
          ))}
        </div>
      </PopoverContent>
    </Popover>
  );
}
