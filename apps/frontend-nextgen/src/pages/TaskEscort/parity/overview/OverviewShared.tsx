import { UnderlineTabs } from '@/components/Admin/Tabs';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Empty } from '@/components/ui/Empty';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/Select';
import { Skeleton } from '@/components/ui/Skeleton';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';
import { overviewStatusLabels } from '@/domain/taskEscortParity/overview';
import type { OverviewReadState } from '@/domain/taskEscortParity/overviewTypes';

export const writeUnavailable = '能力建设中：写入协议与权限确认尚未接入，本页仅支持真实只读查看。';
export function ReadFeedback({
  state,
  label,
}: {
  state: Pick<OverviewReadState<unknown>, 'loading' | 'error' | 'refresh'>;
  label: string;
}) {
  if (state.error)
    return (
      <div
        role="alert"
        className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-destructive/20 bg-destructive/5 p-3 text-xs text-destructive"
      >
        <span>{state.error}</span>
        <Button variant="outline" size="sm" onClick={state.refresh}>
          重试{label}
        </Button>
      </div>
    );
  if (state.loading)
    return (
      <div role="status" aria-label={`${label}加载中`} className="space-y-2 p-4">
        <Skeleton.Block className="h-4 w-1/3" />
        <Skeleton.Block className="h-20 w-full" />
        <span className="sr-only">{label}加载中</span>
      </div>
    );
  return null;
}
export function OverviewStatus({ status }: { status: string | null | undefined }) {
  const tone =
    status === 'succeeded'
      ? 'success'
      : ['failed', 'aborted'].includes(status ?? '')
      ? 'error'
      : ['waiting', 'blocked'].includes(status ?? '')
      ? 'warning'
      : ['running', 'postActionsRunning'].includes(status ?? '')
      ? 'primary'
      : 'neutral';
  return <Badge tone={tone}>{status ? overviewStatusLabels[status] ?? status : '未上报'}</Badge>;
}
export function OverviewSelect({
  label,
  value,
  onChange,
  options,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  options: Array<[string, string]>;
}) {
  return (
    <Select value={value || '__all__'} onValueChange={(next) => onChange(next === '__all__' ? '' : next)}>
      <SelectTrigger aria-label={label} className="w-auto min-w-32">
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {options.map(([value, label]) => (
          <SelectItem key={value || '__all__'} value={value || '__all__'}>
            {label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
export function OverviewTabs<T extends string>({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: T;
  options: Array<[T, string]>;
  onChange: (value: T) => void;
}) {
  return (
    <div
      aria-label={label}
      onKeyDown={(event) => {
        if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
        const tabs = Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="tab"]'));
        const index = tabs.indexOf(event.target as HTMLButtonElement);
        if (index < 0) return;
        event.preventDefault();
        const next =
          event.key === 'Home'
            ? 0
            : event.key === 'End'
            ? tabs.length - 1
            : (index + (event.key === 'ArrowLeft' ? -1 : 1) + tabs.length) % tabs.length;
        onChange(options[next][0]);
        tabs[next]?.focus();
      }}
    >
      <UnderlineTabs
        value={value}
        options={options.map(([value, label]) => ({ value, label }))}
        onChange={onChange}
        className="gap-5 overflow-x-auto px-4"
      />
    </div>
  );
}
export function UnavailableActions({ runId, compact = false }: { runId: string; compact?: boolean }) {
  return (
    <TooltipProvider>
      <div className="flex flex-wrap items-center gap-1">
        <span className="mr-1 text-[10px] font-medium text-muted-foreground">能力建设中</span>
        {['分析', '重跑', '自动修复', '审批', '删除'].map((label) => (
          <Tooltip key={label}>
            <TooltipTrigger asChild>
              <span tabIndex={0} aria-label={`${label}不可用：${writeUnavailable}`}>
                <Button
                  disabled
                  variant="outline"
                  size="sm"
                  aria-label={`${label} ${runId}`}
                  className={compact ? 'h-7 px-1.5 text-[10px]' : ''}
                >
                  {label}
                </Button>
              </span>
            </TooltipTrigger>
            <TooltipContent>
              {label}：{writeUnavailable}
            </TooltipContent>
          </Tooltip>
        ))}
      </div>
    </TooltipProvider>
  );
}
export function EvidenceValue({ label, value }: { label: string; value: unknown }) {
  let display: string | null = null;
  if (value !== null && value !== undefined && value !== '') {
    if (typeof value === 'string') {
      try {
        display = JSON.stringify(JSON.parse(value), null, 2);
      } catch {
        display = value;
      }
    } else display = JSON.stringify(value, null, 2);
  }
  return (
    <section className="min-w-0 space-y-2">
      <h4 className="text-xs font-medium text-foreground">{label}</h4>
      {display === null ? (
        <p className="text-xs text-muted-foreground">接口未提供{label}</p>
      ) : (
        <pre className="tcov-evidence">{display}</pre>
      )}
    </section>
  );
}
export { Button, Empty };
