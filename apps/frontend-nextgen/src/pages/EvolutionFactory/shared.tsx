import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/Select';
import { cn } from '@/utils/cn';
import { history } from '@umijs/max';
import type { ReactNode } from 'react';
import { useCallback, useEffect, useState } from 'react';

// 展示类 primitives（面板 / 骨架 / 徽标 / Tooltip）拆至本目录同名文件；此处聚合再导出，
// 列表页既有 `from './shared'` 调用点保持零改动。
export * from './sharedPrimitives';

/**
 * 「智能进化」首页顶部的胶囊 Tabs —— 与 Bot 管理页（dmore 10/19 稿）同一规范：
 * 激活态 primary 12% 蓝底 + 蓝字 13px/500，未激活灰字，hover 走 muted。
 * 列表页（TaskDetail / DomainDetail 等二级页）继续用 EvolutionHeader 的下划线 tab 表达层级差异。
 */
export type EvolutionTab = 'tasks' | 'templates' | 'versions';
const EVOLUTION_TABS: ReadonlyArray<readonly [EvolutionTab, string]> = [
  ['tasks', '进化任务'],
  ['templates', '评测模板'],
  ['versions', '进化版本'],
];
export function EvolutionTabs({ tab, className }: { tab: EvolutionTab; className?: string }) {
  return (
    <div className={cn('flex items-center gap-1', className)} role="tablist" aria-label="智能进化">
      {EVOLUTION_TABS.map(([value, label]) => (
        <Button
          key={value}
          variant="ghost"
          role="tab"
          type="button"
          aria-selected={tab === value}
          className={cn(
            'inline-flex h-8 items-center rounded-md px-3 text-[13px] transition-colors cursor-pointer',
            tab === value
              ? 'bg-primary/10 font-medium text-primary'
              : 'font-normal text-muted-foreground hover:bg-muted hover:text-foreground',
          )}
          onClick={() => history.push(`/evolution-factory${value === 'tasks' ? '' : `?tab=${value}`}`)}
        >
          {label}
        </Button>
      ))}
    </div>
  );
}

/**
 * 列表筛选下拉：左侧常驻小标题 + 右侧 Select 框（对齐 BotWorkshopToolbar 的 idiom ——
 * 筛选器名作为常驻标题放在框左侧，框内只反映当前选择）。与 Choice 的区别：
 * Choice 服务于表单字段（label 由 Field 承载），FilterSelect 服务于工具行筛选。
 */
export function FilterSelect({
  label,
  value,
  onChange,
  options,
  triggerClassName = 'w-full sm:w-[132px]',
  disabled,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  options: { value: string; label: string }[];
  triggerClassName?: string;
  disabled?: boolean;
}) {
  return (
    <div className="flex w-full min-w-0 items-center gap-2 sm:w-auto">
      <span className="shrink-0 text-xs font-medium text-muted-foreground">{label}</span>
      <Select value={value || undefined} onValueChange={onChange} disabled={disabled}>
        <SelectTrigger aria-label={label} className={triggerClassName}>
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {options
            .filter((item) => item.value)
            .map((item) => (
              <SelectItem key={item.value} value={item.value}>
                {item.label}
              </SelectItem>
            ))}
        </SelectContent>
      </Select>
    </div>
  );
}

export function EvolutionHeader({
  tab,
  actions,
  showTabs = true,
}: {
  tab: 'tasks' | 'templates' | 'versions';
  actions?: ReactNode;
  showTabs?: boolean;
}) {  return (
    <div className="space-y-5">
      <header className="flex items-center justify-between gap-3">
        <h1 className="text-2xl font-semibold">智能进化</h1>
        {actions}
      </header>
      {showTabs && (
        <div role="tablist" aria-label="智能进化" className="flex gap-6 border-b border-divider">
          {(
            [
              ['tasks', '进化任务'],
              ['templates', '评测模板'],
              ['versions', '进化版本'],
            ] as const
          ).map(([value, label]) => (
            <Button
              variant="ghost"
              style={{ height: 'auto', whiteSpace: 'normal' }}
              key={value}
              type="button"
              role="tab"
              aria-selected={tab === value}
              className={`border-b-2 px-3 pb-3 text-sm ${
                tab === value ? 'border-primary font-medium text-primary' : 'border-transparent text-muted-foreground'
              }`}
              onClick={() => history.push(`/evolution-factory${value === 'tasks' ? '' : `?tab=${value}`}`)}
            >
              {label}
            </Button>
          ))}
        </div>
      )}
    </div>
  );
}

export function useResource<T>(load: () => Promise<T>, enabled = true) {
  const [data, setData] = useState<T>();
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(enabled);
  const [revision, setRevision] = useState(0);
  const reload = useCallback(() => setRevision((value) => value + 1), []);
  useEffect(() => {
    let current = true;
    setData(undefined);
    setError('');
    if (!enabled) {
      setLoading(false);
      return;
    }
    setLoading(true);
    load()
      .then((value) => {
        if (current) setData(value);
      })
      .catch((e: unknown) => {
        if (current) setError(e instanceof Error ? e.message : '加载失败');
      })
      .finally(() => {
        if (current) setLoading(false);
      });
    return () => {
      current = false;
    };
  }, [load, enabled, revision]);
  return { data, error, loading, reload };
}
export function Section({ title, children, index }: { title: string; children: ReactNode; index?: number }) {
  return (
    <Card className="p-5 sm:p-6">
      <h2 className="mb-4 flex items-center gap-2 text-[15px] font-semibold">
        {index !== undefined && (
          <span className="flex h-[22px] w-[22px] shrink-0 items-center justify-center rounded-full bg-primary text-xs text-primary-foreground">
            {index}
          </span>
        )}
        {title}
      </h2>
      {children}
    </Card>
  );
}
export function Field({ label, help, children }: { label: string; help?: string; children: ReactNode }) {
  return (
    <label className="flex min-w-0 flex-col gap-2 text-sm">
      <span className="font-medium">{label}</span>
      {children}
      {help && <span className="text-xs text-muted-foreground">{help}</span>}
    </label>
  );
}
export function Choice({
  value,
  onChange,
  options,
  label,
  disabled,
}: {
  value: string;
  onChange: (value: string) => void;
  options: { value: string; label: string }[];
  label: string;
  disabled?: boolean;
}) {
  return (
    <Select value={value || undefined} onValueChange={onChange} disabled={disabled}>
      <SelectTrigger aria-label={label}>
        <SelectValue placeholder="请选择" />
      </SelectTrigger>
      <SelectContent>
        {options
          .filter((item) => item.value)
          .map((item) => (
            <SelectItem key={item.value} value={item.value}>
              {item.label}
            </SelectItem>
          ))}
      </SelectContent>
    </Select>
  );
}