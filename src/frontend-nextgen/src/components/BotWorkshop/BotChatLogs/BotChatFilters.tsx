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
    <div className="rounded-xl bg-[var(--color-panel-muted)] p-5">
      <div className="flex flex-wrap items-center gap-x-6 gap-y-4">
        <div className="flex items-center gap-2">
          <span className="whitespace-nowrap text-sm text-[var(--color-muted)]">时间：</span>
          <div className="flex items-center gap-2 rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] px-2">
            <Input
              aria-label="开始时间"
              className="w-[178px] border-0 bg-transparent px-1 focus:ring-0"
              type="datetime-local"
              value={value.fromDate}
              onChange={(event) => onChange('fromDate', event.target.value)}
              onKeyDown={onKeyDown}
            />
            <span className="text-xs text-[var(--color-muted)]">~</span>
            <Input
              aria-label="结束时间"
              className="w-[178px] border-0 bg-transparent px-1 focus:ring-0"
              type="datetime-local"
              value={value.toDate}
              onChange={(event) => onChange('toDate', event.target.value)}
              onKeyDown={onKeyDown}
            />
          </div>
        </div>
        {fields.map((field) => (
          <label key={field.key} className="flex items-center gap-2 text-sm text-[var(--color-muted)]">
            <span className="whitespace-nowrap">{field.label}：</span>
            <Input
              className={field.className ?? 'w-48'}
              value={value[field.key]}
              placeholder={field.key === 'keyword' ? 'Input/Output' : '请输入'}
              onChange={(event) => onChange(field.key, event.target.value)}
              onKeyDown={onKeyDown}
            />
          </label>
        ))}
        <div className="ml-auto flex items-center gap-2">
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
