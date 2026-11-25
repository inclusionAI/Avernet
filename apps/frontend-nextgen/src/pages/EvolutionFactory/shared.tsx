import { Button } from '@/components/ui/Button';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/Select';
import { cn } from '@/utils/cn';
import { history } from '@umijs/max';
import { Info } from 'lucide-react';
import type { ReactNode } from 'react';
import { useCallback, useEffect, useState } from 'react';
import { Hint } from './sharedPrimitives';

// 展示类 primitives（面板 / 骨架 / 徽标 / Tooltip）拆至本目录同名文件；此处聚合再导出，
// 列表页既有 `from './shared'` 调用点保持零改动。
export * from './sharedPrimitives';

/** Shared navigation: blue text on a light blue pill, with no underline or border. */
export type EvolutionTab = 'tasks' | 'templates' | 'versions';
const EVOLUTION_TABS: ReadonlyArray<readonly [EvolutionTab, string]> = [
  ['tasks', '进化任务'],
  ['templates', '评测模板'],
  ['versions', '进化版本'],
];
export function EvolutionTabs({ tab, className }: { tab: EvolutionTab; className?: string }) {
  return (
    <div className={cn('flex items-center gap-2', className)} role="tablist" aria-label="智能进化">
      {EVOLUTION_TABS.map(([value, label]) => (
        <Button
          key={value}
          variant="ghost"
          role="tab"
          type="button"
          aria-selected={tab === value}
          className={cn(
            'inline-flex h-8 items-center rounded-md border-0 px-3 text-[13px] transition-colors cursor-pointer',
            tab === value
              ? 'bg-primary/10 font-medium text-primary hover:bg-primary/10 hover:text-primary'
              : 'bg-transparent font-normal text-muted-foreground hover:bg-muted hover:text-foreground',
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
  triggerClassName = 'h-8 w-full sm:w-[132px]',
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
      <span className="shrink-0 text-[13px] font-normal text-foreground">{label}</span>
      <Select value={value} onValueChange={onChange} disabled={disabled}>
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
}) {
  return (
    <div className="space-y-5">
      <header className="flex items-center justify-between gap-3">
        <h1 className="text-2xl font-semibold">智能进化</h1>
        {actions}
      </header>
      {showTabs && <EvolutionTabs tab={tab} />}
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
export function Section({ title, children }: { title: string; children: ReactNode; index?: number }) {
  return (
    <section className="min-w-0">
      <h2 className="mb-3 flex items-center gap-2 text-sm font-semibold text-foreground">
        <span aria-hidden className="h-3.5 w-0.5 shrink-0 bg-primary" />
        {title}
      </h2>
      {children}
    </section>
  );
}
export function Field({
  label,
  help,
  labelHint,
  children,
}: {
  label: string;
  help?: string;
  labelHint?: string;
  children: ReactNode;
}) {
  return (
    <label className="flex min-w-0 flex-col gap-2 text-sm">
      <span className="text-[13px] font-normal text-foreground">
        {label.endsWith(' *') ? (
          <>
            <span aria-hidden="true" className="mr-1 text-destructive">
              *
            </span>
            {label.slice(0, -2)}
          </>
        ) : (
          label
        )}
        {labelHint && (
          <Hint content={labelHint}>
            <span aria-label="填写说明" className="ml-2 inline-flex align-middle text-muted-foreground">
              <Info aria-hidden className="size-3.5" />
            </span>
          </Hint>
        )}
      </span>
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
    <Select value={value} onValueChange={onChange} disabled={disabled}>
      <SelectTrigger aria-label={label} className="h-8">
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
