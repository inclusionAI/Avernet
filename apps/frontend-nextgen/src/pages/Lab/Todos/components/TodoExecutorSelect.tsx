import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/Select';
import type { TodoExecutor, TodoExecutorOption } from '@/domain/todos/models';

interface TodoExecutorSelectProps {
  options: TodoExecutorOption[];
  value?: TodoExecutor;
  onChange: (executor: TodoExecutor) => void;
  disabled?: boolean;
  ariaLabel?: string;
  className?: string;
}

function optionValue(option: Pick<TodoExecutor, 'type' | 'id'>): string {
  return `${option.type}:${option.id}`;
}

export function TodoExecutorSelect({
  options,
  value,
  onChange,
  disabled,
  ariaLabel = '选择执行方',
  className,
}: TodoExecutorSelectProps) {
  const selectedValue = value ? optionValue(value) : undefined;
  return (
    <Select
      value={selectedValue}
      disabled={disabled || options.length === 0}
      onValueChange={(nextValue) => {
        const option = options.find((item) => optionValue(item) === nextValue);
        if (!option) return;
        onChange({ type: option.type, id: option.id, ...(option.revisionId ? { revisionId: option.revisionId } : {}) });
      }}
    >
      <SelectTrigger aria-label={ariaLabel} className={className}>
        <SelectValue placeholder={options.length ? '请选择执行方' : '当前空间暂无可用执行方'} />
      </SelectTrigger>
      <SelectContent>
        {options.map((option) => (
          <SelectItem key={optionValue(option)} value={optionValue(option)}>
            {option.type === 'bot_team' ? `Bot 小队 · ${option.name}` : `Bot · ${option.name}`}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
