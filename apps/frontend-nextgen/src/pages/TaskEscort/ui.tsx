import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { ModalContent as ProjectModalContent } from '@/components/ui/Modal';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/Select';
import { Search } from 'lucide-react';
import React from 'react';
export { PageHeader } from '@/components/Common/PageHeader';
export { Empty } from '@/components/ui/Empty';
export { Modal, ModalDescription, ModalFooter, ModalHeader, ModalTitle } from '@/components/ui/Modal';
export { Switch } from '@/components/ui/Switch';
export { Textarea } from '@/components/ui/Textarea';
export { Badge, Button, Input, Select, SelectContent, SelectItem, SelectTrigger, SelectValue };
/** Keep presentation-only helpers scoped even when project dialogs portal to body. */
export const ModalContent = React.forwardRef<
  React.ElementRef<typeof ProjectModalContent>,
  React.ComponentPropsWithoutRef<typeof ProjectModalContent>
>(({ className = '', ...props }, ref) => (
  <ProjectModalContent ref={ref} className={`task-escort-workspace task-escort-dialog ${className}`} {...props} />
));
ModalContent.displayName = 'TaskEscortModalContent';

export function Panel({
  title,
  description,
  action,
  children,
  className = '',
}: {
  title?: string;
  description?: React.ReactNode;
  action?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <section className={`panel ${className}`}>
      {(title || action) && (
        <div className="mb-5 flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 className="section-title">{title}</h2>
            {description && <p className="mt-1.5 text-xs leading-5 text-muted-foreground">{description}</p>}
          </div>
          {action}
        </div>
      )}
      {children}
    </section>
  );
}
export function SectionTitle({ children }: { children: React.ReactNode }) {
  return <h2 className="section-title">{children}</h2>;
}
const statuses: Record<
  string,
  {
    label: string;
    tone: 'success' | 'error' | 'primary' | 'warning' | 'neutral';
  }
> = {
  unknown: { label: '未知', tone: 'neutral' },
  success: { label: '运行成功', tone: 'success' },
  failed: { label: '运行失败', tone: 'error' },
  running: { label: '运行中', tone: 'primary' },
  waiting: { label: '等待中', tone: 'warning' },
  cancelled: { label: '已取消', tone: 'neutral' },
  open: { label: '待处理', tone: 'warning' },
  applied: { label: '待验证', tone: 'primary' },
  verified: { label: '已验证', tone: 'success' },
  recurring: { label: '再次复发', tone: 'error' },
  healthy: { label: '健康', tone: 'success' },
  warning: { label: '需关注', tone: 'warning' },
};
export function StatusBadge({ status }: { status: string }) {
  const entry = statuses[status] ?? { label: status, tone: 'neutral' as const };
  return (
    <Badge tone={entry.tone} className="gap-1.5 whitespace-nowrap">
      <span className="size-1.5 rounded-full bg-current" />
      {entry.label}
    </Badge>
  );
}
export function SearchField({
  value,
  onChange,
  placeholder = '搜索工作流、问题或运行 ID',
}: {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
}) {
  return (
    <div className="relative min-w-48 flex-1">
      <Search className="absolute left-3 top-2.5 size-3.5 text-muted-foreground" aria-hidden />
      <Input
        aria-label={placeholder}
        className="h-9 pl-9"
        placeholder={placeholder}
        value={value}
        onChange={(e) => onChange(e.target.value)}
      />
    </div>
  );
}
export function SelectField({
  value,
  onChange,
  options,
  label,
}: {
  value: string;
  onChange: (value: string) => void;
  options: { value: string; label: string }[];
  label: string;
}) {
  return (
    <Select value={value} onValueChange={onChange}>
      <SelectTrigger aria-label={label} className="w-auto min-w-32">
        <SelectValue placeholder={label} />
      </SelectTrigger>
      <SelectContent>
        {options.map((option) => (
          <SelectItem key={option.value} value={option.value}>
            {option.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
export function IconButton({ label, icon, onClick }: { label: string; icon: React.ReactNode; onClick?: () => void }) {
  return (
    <Button size="icon" variant="ghost" aria-label={label} onClick={onClick}>
      {icon}
    </Button>
  );
}
export function MiniLine({
  values,
  color = 'primary',
  height = 40,
}: {
  values: number[];
  color?: string;
  height?: number;
}) {
  const max = Math.max(...values, 1),
    min = Math.min(...values, 0);
  const range = max - min || 1;
  const points = values
    .map((v, i) => `${(i * 160) / Math.max(1, values.length - 1)},${height - 3 - ((v - min) / range) * (height - 6)}`)
    .join(' ');
  return (
    <svg
      width="100%"
      height={height}
      viewBox={`0 0 160 ${height}`}
      preserveAspectRatio="none"
      aria-label="趋势迷你图"
      role="img"
      className={`text-${color}`}
    >
      <polyline points={points} fill="none" stroke="currentColor" strokeWidth="1.8" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}
export function DemoNotice() {
  return (
    <div className="flex items-center gap-2 text-[11px] text-muted-foreground">
      <span className="size-1.5 rounded-full bg-primary" />
      交互演示 · 所有数据均为模拟，不连接线上服务
    </div>
  );
}
