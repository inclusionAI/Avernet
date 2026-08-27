import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/Card';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import { Empty } from '@/components/ui/Empty';
import { Input } from '@/components/ui/Input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/Select';
import type { ScheduledRoutineRecord } from '@/services/scheduledTasks';
import { cn } from '@/utils/cn';
import { Eye, Play, Search } from 'lucide-react';
import React, { useMemo, useState } from 'react';
import { toast } from 'sonner';

export interface RoutineBotOption {
  value: string;
  label: string;
}

export interface RoutineTaskTabProps {
  routines: ScheduledRoutineRecord[];
  loading: boolean;
  error: string | null;
  botOptions: RoutineBotOption[];
  selectedBotId: string;
  onChangeBotId: (botId: string) => void;
  onRetry: () => void;
  onSelectRoutine: (routine: ScheduledRoutineRecord) => void;
  onRunRoutine: (routine: ScheduledRoutineRecord) => Promise<unknown>;
}

function Th({ className, ...props }: React.ThHTMLAttributes<HTMLTableCellElement>) {
  return <th className={cn('px-4 py-3 text-xs font-medium', className)} {...props} />;
}

function Td({ className, ...props }: React.TdHTMLAttributes<HTMLTableCellElement>) {
  return <td className={cn('border-t border-[var(--color-border)] px-4 py-4 align-top', className)} {...props} />;
}

function formatDateTime(value?: string | null): string {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  const hours = String(date.getHours()).padStart(2, '0');
  const minutes = String(date.getMinutes()).padStart(2, '0');
  const seconds = String(date.getSeconds()).padStart(2, '0');
  return `${year}-${month}-${day} ${hours}:${minutes}:${seconds}`;
}

export function RoutineTaskTab({
  routines,
  loading,
  error,
  botOptions,
  selectedBotId,
  onChangeBotId,
  onRetry,
  onSelectRoutine,
  onRunRoutine,
}: RoutineTaskTabProps) {
  const [routineKeyword, setRoutineKeyword] = useState('');

  const routineList = useMemo(() => {
    const keyword = routineKeyword.trim().toLowerCase();
    return routines.filter((item) => {
      return (
        !keyword ||
        item.name.toLowerCase().includes(keyword) ||
        item.botName.toLowerCase().includes(keyword) ||
        item.botId.toLowerCase().includes(keyword) ||
        item.prompt?.toLowerCase().includes(keyword) ||
        item.id.toLowerCase().includes(keyword) ||
        item.frequency.toLowerCase().includes(keyword)
      );
    });
  }, [routineKeyword, routines]);

  return (
    <section className="space-y-4">
      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <CardTitle>定时任务</CardTitle>
              <CardDescription>
                可选择「全部」查看当前账号下所有 Bot 的定时任务，也可以切换到单个 Bot 精确筛选。数据由后端 routines
                接口按 Bot 维度拉取并合并。
              </CardDescription>
            </div>
            <Badge tone="primary">实时接口</Badge>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex flex-col gap-3 xl:flex-row xl:items-center xl:justify-between">
            <div className="flex items-center gap-2 xl:w-[26rem]">
              <div className="relative flex-1">
                <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-[var(--color-muted)]" />
                <Input
                  value={routineKeyword}
                  onChange={(event) => setRoutineKeyword(event.target.value)}
                  placeholder="搜索任务名称 / 提示词 / 频率"
                  className="pl-9"
                />
              </div>
            </div>
            <div className="flex items-center gap-2 xl:min-w-[18rem] xl:justify-end">
              <span className="shrink-0 text-sm text-[var(--color-muted)]">Bot</span>
              <Select value={selectedBotId} onValueChange={onChangeBotId}>
                <SelectTrigger className="w-full xl:w-[18rem]">
                  <SelectValue placeholder="请选择 Bot" />
                </SelectTrigger>
                <SelectContent>
                  {botOptions.map((item) => (
                    <SelectItem key={item.value} value={item.value}>
                      {item.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
          {error && routineList.length > 0 ? (
            <div className="rounded-lg border border-[var(--color-warning-soft)] bg-[var(--color-warning-soft)]/40 px-3 py-2 text-sm text-[var(--color-warning)]">
              {error}
            </div>
          ) : null}
        </CardContent>
      </Card>

      <Card>
        <div className="overflow-hidden">
          <div className="app-scrollbar overflow-x-auto">
            <table className="min-w-full border-separate border-spacing-0">
              <thead>
                <tr className="bg-[var(--color-panel-strong)]/60 text-left text-xs font-medium text-[var(--color-muted)]">
                  <Th className="rounded-tl-xl">任务</Th>
                  <Th>Owner Bot / 模型</Th>
                  <Th>频率 / 时区</Th>
                  <Th>下次执行</Th>
                  <Th>最近执行</Th>
                  <Th className="rounded-tr-xl text-right">操作</Th>
                </tr>
              </thead>
              <tbody>
                {loading ? (
                  <tr>
                    <td colSpan={6} className="p-0">
                      <div className="py-16 text-center text-sm text-[var(--color-muted)]">定时任务加载中…</div>
                    </td>
                  </tr>
                ) : error && routineList.length === 0 ? (
                  <tr>
                    <td colSpan={6} className="p-0">
                      <Empty
                        title="定时任务加载失败"
                        description={error}
                        action={
                          <Button variant="secondary" onClick={onRetry}>
                            重试
                          </Button>
                        }
                      />
                    </td>
                  </tr>
                ) : routineList.length === 0 ? (
                  <tr>
                    <td colSpan={6} className="p-0">
                      <Empty title="暂无符合条件的定时任务" description="当前账号下还没有可展示的定时任务。" />
                    </td>
                  </tr>
                ) : (
                  routineList.map((item) => (
                    <tr
                      key={`${item.botId}-${item.id}`}
                      className="border-b border-[var(--color-border)] text-xs transition-colors hover:bg-[var(--color-panel-muted)]/60"
                    >
                      <Td className="max-w-[24rem]">
                        <div className="space-y-1">
                          <div className="truncate font-medium text-[var(--color-fg)]">{item.name}</div>
                          {item.prompt ? (
                            <p className="m-0 line-clamp-2 text-xs leading-5 text-[var(--color-muted)]">
                              {item.prompt}
                            </p>
                          ) : null}
                          <div className="flex flex-wrap items-center gap-2 text-xs text-[var(--color-muted)]">
                            <code className="rounded bg-[var(--color-panel-strong)] px-1.5 py-0.5">{item.id}</code>
                          </div>
                        </div>
                      </Td>
                      <Td>
                        <div className="space-y-1">
                          <div className="text-[var(--color-fg)]">
                            <span className="font-medium">{item.botName}</span>
                          </div>
                          <div className="text-xs text-[var(--color-muted)]">Bot ID：{item.botId}</div>
                          <div className="text-xs text-[var(--color-muted)]">模型：{item.model}</div>
                        </div>
                      </Td>
                      <Td>
                        <div className="space-y-1 text-xs text-[var(--color-fg)]">
                          <div>{item.frequency}</div>
                          <div className="text-xs text-[var(--color-muted)]">{item.timezone ?? '—'}</div>
                        </div>
                      </Td>
                      <Td>
                        <div className="text-xs text-[var(--color-fg)]">{formatDateTime(item.nextRunAt)}</div>
                      </Td>
                      <Td>
                        <div className="text-xs text-[var(--color-fg)]">{formatDateTime(item.lastRunAt)}</div>
                      </Td>
                      <Td>
                        <div className="flex justify-end gap-2" onClick={(event) => event.stopPropagation()}>
                          <Button
                            variant="secondary"
                            size="sm"
                            leftIcon={<Eye className="size-4" />}
                            onClick={() => onSelectRoutine(item)}
                          >
                            查看实例
                          </Button>
                          <ConfirmDialog
                            title={`立即触发「${item.name}」`}
                            description="当前页面会调用真实 routines 接口触发一次执行。"
                            confirmText="立即触发"
                            onConfirm={async () => {
                              try {
                                await onRunRoutine(item);
                                toast.success(`已触发一次 ${item.name}`);
                              } catch (err) {
                                const message = err instanceof Error ? err.message : '定时任务触发失败';
                                toast.error(message);
                              }
                            }}
                          >
                            <Button variant="primary" size="sm" leftIcon={<Play className="size-4" />}>
                              立即触发
                            </Button>
                          </ConfirmDialog>
                        </div>
                      </Td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>
      </Card>
    </section>
  );
}
