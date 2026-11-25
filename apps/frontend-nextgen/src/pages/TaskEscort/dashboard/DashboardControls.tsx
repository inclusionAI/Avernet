import { Button } from '@/components/ui/Button';
import { Empty } from '@/components/ui/Empty';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/Select';
import { Skeleton } from '@/components/ui/Skeleton';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';
import { dashboardRangeLabels } from '@/domain/taskEscortWorkspace/dashboard';
import type { DashboardResource, DashboardWidgetRange } from '@/domain/taskEscortWorkspace/dashboardTypes';
import type { ReactElement, ReactNode } from 'react';

export function DashboardSelect<T extends string>({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: T;
  options: Array<{ value: T; label: string }>;
  onChange: (value: T) => void;
}) {
  return (
    <Select value={value} onValueChange={(next) => onChange(next as T)}>
      <SelectTrigger aria-label={label} className="h-8 w-auto min-w-32 max-w-full text-xs">
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {options.map((item) => (
          <SelectItem key={item.value} value={item.value}>
            {item.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
export function DashboardWidgetSelector({
  title,
  value,
  globalLabel,
  onChange,
}: {
  title: string;
  value: DashboardWidgetRange;
  globalLabel: string;
  onChange: (value: DashboardWidgetRange) => void;
}) {
  return (
    <DashboardSelect
      label={`${title}时间范围`}
      value={value}
      onChange={onChange}
      options={[
        { value: 'global', label: `跟随全局（${globalLabel}）` },
        { value: 'yesterday', label: dashboardRangeLabels.yesterday },
        { value: '7d', label: dashboardRangeLabels['7d'] },
        { value: '30d', label: dashboardRangeLabels['30d'] },
      ]}
    />
  );
}
export function DashboardReadState<T>({
  resource,
  title,
  children,
}: {
  resource: DashboardResource<T>;
  title: string;
  children: (data: T) => ReactNode;
}) {
  if (resource.loading)
    return (
      <div role="status" aria-label={`${title}加载中`} className="space-y-3 py-5">
        <span className="sr-only">{title}加载中</span>
        <Skeleton.Block className="h-6 w-2/3" />
        <Skeleton.Block className="h-24 w-full" />
      </div>
    );
  if (resource.error)
    return (
      <div role="alert" className="space-y-3 py-6 text-sm">
        <p className="font-medium text-destructive">{title}读取失败</p>
        <p className="break-words text-muted-foreground">{resource.error}</p>
        <Button size="sm" variant="outline" onClick={resource.retry}>
          重试{title}
        </Button>
      </div>
    );
  if (!resource.data) return <Empty compact title="暂无数据" description={`${title}尚未返回真实数据`} />;
  return <>{children(resource.data)}</>;
}

export function DashboardTooltip({ label, children }: { label: string; children: ReactElement }) {
  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>{children}</TooltipTrigger>
        <TooltipContent>{label}</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}
