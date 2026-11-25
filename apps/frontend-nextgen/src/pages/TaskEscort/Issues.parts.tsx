import { ArrowRight, SearchX } from 'lucide-react';
import type { Issue } from './types';
import { Badge, Button, Empty } from './ui';

export function SeverityBadge({ severity }: { severity: Issue['severity'] }) {
  const styles = {
    high: 'border-destructive/20 bg-destructive/5 text-destructive',
    medium: 'border-warning/25 bg-warning/10 text-warning',
    low: 'border-border bg-muted text-muted-foreground',
  };
  const labels = { high: '高影响', medium: '中影响', low: '低影响' };
  return (
    <Badge tone="outline" className={`gap-1.5 font-normal ${styles[severity]}`}>
      <span className="size-1.5 rounded-full bg-current" aria-hidden="true" />
      {labels[severity]}
    </Badge>
  );
}

export function IssueEmpty({ onClear }: { onClear: () => void }) {
  return (
    <div role="status">
      <Empty
        title="没有符合条件的异常"
        description="试试其他关键词，或清除筛选查看全部记录。"
        icon={<SearchX className="size-5" />}
        action={
          <Button variant="outline" size="sm" onClick={onClear}>
            清除筛选
            <ArrowRight className="ml-2 size-3.5" />
          </Button>
        }
      />
    </div>
  );
}

export function IssueLifecycle({ current }: { current: number }) {
  return (
    <ol
      className="grid grid-cols-2 gap-3 rounded-lg border border-border bg-muted/30 p-4 sm:grid-cols-4"
      aria-label="建议处理进度"
    >
      {['发现异常', '应用建议', '人工验证', '沉淀经验'].map((label, index) => (
        <li
          key={label}
          className="flex items-center gap-2 text-xs"
          aria-current={index === current ? 'step' : undefined}
        >
          <span
            className={`flex size-6 shrink-0 items-center justify-center rounded-full text-[11px] font-semibold ${
              index <= current ? 'bg-primary/10 text-primary' : 'bg-muted text-muted-foreground'
            }`}
          >
            {index + 1}
          </span>
          <span className={index <= current ? 'font-medium text-foreground' : 'text-muted-foreground'}>{label}</span>
        </li>
      ))}
    </ol>
  );
}
