import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import type { BotChatFilters as Filters } from '@/domain/botChats';
import { RotateCcw, Search } from 'lucide-react';

interface Props {
  value: Filters;
  loading: boolean;
  onChange: (key: keyof Filters, value: string) => void;
  onQuery: () => void;
  onReset: () => void;
}

const fields: { key: keyof Filters; label: string; className?: string }[] = [
  { key: 'bizScene', label: '业务场景' },
  { key: 'bizTaskId', label: '业务任务' },
  { key: 'groupId', label: '群ID', className: 'w-40' },
  { key: 'sessionId', label: 'sessionID' },
  { key: 'sessionKey', label: 'sessionKey' },
  { key: 'traceId', label: 'traceID' },
  { key: 'keyword', label: '输入/输出', className: 'w-52' },
];

export function BotChatFilters({ value, loading, onChange, onQuery, onReset }: Props) {
  const onKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'Enter') onQuery();
  };

  return (
    <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-panel-muted)] p-4 shadow-sm sm:p-5">
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-4 2xl:grid-cols-5">
        <div className="flex min-w-0 items-center gap-2 2xl:col-span-2">
          <span className="whitespace-nowrap text-sm text-[var(--color-muted)]">时间：</span>
          <div className="flex min-w-0 flex-1 items-center gap-2 rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] px-2">
            <Input
              aria-label="开始时间"
              className="min-w-0 flex-1 border-0 bg-transparent px-1 focus:ring-0"
              type="datetime-local"
              value={value.fromDate}
              onChange={(event) => onChange('fromDate', event.target.value)}
              onKeyDown={onKeyDown}
            />
            <span className="text-xs text-[var(--color-muted)]">~</span>
            <Input
              aria-label="结束时间"
              className="min-w-0 flex-1 border-0 bg-transparent px-1 focus:ring-0"
              type="datetime-local"
              value={value.toDate}
              onChange={(event) => onChange('toDate', event.target.value)}
              onKeyDown={onKeyDown}
            />
          </div>
        </div>
        {fields.map((field) => (
          <label key={field.key} className="flex min-w-0 items-center gap-2 text-sm text-[var(--color-muted)]">
            <span className="whitespace-nowrap">{field.label}：</span>
            <Input
              className={field.className ? `${field.className} max-w-full flex-1` : 'min-w-0 flex-1'}
              value={value[field.key]}
              placeholder={field.key === 'keyword' ? 'Input/Output' : '请输入'}
              onChange={(event) => onChange(field.key, event.target.value)}
              onKeyDown={onKeyDown}
            />
          </label>
        ))}
        <div className="flex items-center justify-end gap-2 md:col-span-2 xl:col-span-4 2xl:col-span-1">
          <Button loading={loading} leftIcon={<Search className="size-4" />} onClick={onQuery}>
            查询
          </Button>
          <Button variant="ghost" leftIcon={<RotateCcw className="size-4" />} onClick={onReset}>
            重置
          </Button>
        </div>
      </div>
    </div>
  );
}
