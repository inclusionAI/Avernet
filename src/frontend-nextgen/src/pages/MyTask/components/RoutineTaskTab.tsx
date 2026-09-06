import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import { Empty } from '@/components/ui/Empty';
import { Input } from '@/components/ui/Input';
import { Pagination } from '@/components/ui/Pagination';
import type { ScheduledRoutineRecord } from '@/services/scheduledTasks';
import { cn } from '@/utils/cn';
import { Eye, Play, Search } from 'lucide-react';
import React, { useMemo, useState } from 'react';
import { toast } from 'sonner';
import { makeRoutineKey } from '../hooks/routineTaskUtils';
import { formatDateTime, getBotDisplayName } from '../userTaskUtils';
import { RoutineBotSelector, type RoutineBotOption } from './RoutineBotSelector';

export interface RoutineTaskTabProps {
  routines: ScheduledRoutineRecord[];
  total: number;
  page: number;
  pageSize: number;
  loading: boolean;
  error: string | null;
  botOptions: RoutineBotOption[];
  selectedBotId: string;
  showBotSelector?: boolean;
  onChangeBotId: (botId: string) => void;
  onRetry: () => void;
  onSelectRoutine: (routine: ScheduledRoutineRecord) => void;
  onRunRoutine: (routine: ScheduledRoutineRecord) => Promise<unknown>;
  onPageChange: (page: number) => void;
  onPageSizeChange: (pageSize: number) => void;
  botNameMap: Record<string, string>;
}

/** runtime_stage 标签颜色：draft 草稿 / verify 验证 / online 线上。 */
function getRoutineStageTone(stage?: string): 'neutral' | 'warning' | 'success' | 'outline' {
  switch (stage) {
    case 'draft':
      return 'neutral';
    case 'verify':
      return 'warning';
    case 'online':
      return 'success';
    default:
      return 'outline';
  }
}

function Th({ className, ...props }: React.ThHTMLAttributes<HTMLTableCellElement>) {
  return <th className={cn('h-10 px-4 py-3 text-xs font-medium text-muted-foreground', className)} {...props} />;
}

function Td({ className, ...props }: React.TdHTMLAttributes<HTMLTableCellElement>) {
  return <td className={cn('border-t border-border px-4 py-3 align-top text-xs', className)} {...props} />;
}

export function RoutineTaskTab({
  routines,
  total,
  page,
  pageSize,
  loading,
  error,
  botOptions,
  selectedBotId,
  showBotSelector = true,
  onChangeBotId,
  onRetry,
  onSelectRoutine,
  onRunRoutine,
  onPageChange,
  onPageSizeChange,
  botNameMap,
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

  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  const safePage = Math.min(page, pageCount);
  const visibleRoutines = routineList;
  const visibleTotal = total;

  return (
    <section className="space-y-4">
      <div className="space-y-3">
        <div
          className={cn(
            'grid gap-3 border-y border-border py-3',
            showBotSelector ? 'md:grid-cols-3' : 'md:grid-cols-1',
          )}
        >
          <div className="relative min-w-0">
            <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              value={routineKeyword}
              onChange={(event) => {
                setRoutineKeyword(event.target.value);
                onPageChange(1);
              }}
              placeholder="搜索任务名称 / 提示词 / 频率"
              className="pl-9 text-xs"
            />
          </div>
          {showBotSelector ? (
            <RoutineBotSelector options={botOptions} value={selectedBotId} onChange={onChangeBotId} />
          ) : null}
        </div>
        {error && routineList.length > 0 ? (
          <div className="rounded-lg border border-warning/30 bg-warning/5 px-3 py-2 text-xs text-warning">{error}</div>
        ) : null}
      </div>

      <Card className="overflow-hidden rounded-lg shadow-sm">
        <div className="overflow-hidden bg-card">
          <div className="app-scrollbar overflow-x-auto">
            <table className="min-w-full border-separate border-spacing-0">
              <thead>
                <tr className="bg-muted/30 text-left text-xs font-medium text-muted-foreground">
                  <Th>任务</Th>
                  <Th>Owner Bot / 模型</Th>
                  <Th>频率 / 时区</Th>
                  <Th>下次执行</Th>
                  <Th>最近执行</Th>
                  <Th>是否启用</Th>
                  <Th className="text-right">操作</Th>
                </tr>
              </thead>
              <tbody>
                {loading ? (
                  <tr>
                    <td colSpan={7} className="p-0">
                      <div className="py-16 text-center text-xs text-muted-foreground">定时任务加载中…</div>
                    </td>
                  </tr>
                ) : error && routineList.length === 0 ? (
                  <tr>
                    <td colSpan={7} className="p-0">
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
                ) : visibleRoutines.length === 0 ? (
                  <tr>
                    <td colSpan={7} className="p-0">
                      <Empty title="暂无符合条件的定时任务" description="当前账号下还没有可展示的定时任务。" />
                    </td>
                  </tr>
                ) : (
                  visibleRoutines.map((item) => {
                    const ownerBotName = getBotDisplayName(botNameMap, item.botId, item.botName);
                    return (
                      <tr
                        key={makeRoutineKey(item.botId, item.id, item.runtimeStage)}
                        className="border-b border-border text-xs transition-colors hover:bg-muted/50"
                      >
                        <Td className="max-w-[24rem]">
                          <div className="space-y-1">
                            <div className="truncate font-medium text-foreground">{item.name}</div>
                            {item.prompt ? (
                              <p className="m-0 line-clamp-2 text-xs leading-5 text-muted-foreground">{item.prompt}</p>
                            ) : null}
                            <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                              <code className="rounded bg-muted px-1.5 py-0.5 font-mono">{item.id}</code>
                              {item.runtimeStage ? (
                                <Badge tone={getRoutineStageTone(item.runtimeStage)} className="px-1.5 text-[10px]">
                                  {item.runtimeStage}
                                </Badge>
                              ) : null}
                            </div>
                          </div>
                        </Td>
                        <Td>
                          <div className="space-y-1">
                            <div className="text-foreground">
                              <span className="font-medium">{ownerBotName}</span>
                            </div>
                            <div className="text-xs text-muted-foreground">Bot ID：{item.botId}</div>
                            <div className="text-xs text-muted-foreground">模型：{item.model}</div>
                          </div>
                        </Td>
                        <Td>
                          <div className="space-y-1 text-xs text-foreground">
                            <div>{item.frequency}</div>
                            <div className="text-xs text-muted-foreground">{item.timezone ?? '—'}</div>
                          </div>
                        </Td>
                        <Td>
                          <div className="text-xs text-foreground">{formatDateTime(item.nextRunAt)}</div>
                        </Td>
                        <Td>
                          <div className="text-xs text-foreground">{formatDateTime(item.lastRunAt)}</div>
                        </Td>
                        <Td>
                          <div className="text-xs text-foreground">
                            {item.enabled === true ? '是' : item.enabled === false ? '否' : '—'}
                          </div>
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
                              disabled={item.enabled === false}
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
                              <Button
                                variant="primary"
                                size="sm"
                                leftIcon={<Play className="size-4" />}
                                disabled={item.enabled === false}
                                title={item.enabled === false ? '定时任务未启用，不能立即触发' : undefined}
                              >
                                立即触发
                              </Button>
                            </ConfirmDialog>
                          </div>
                        </Td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>
        </div>
        <div className="px-4 pb-4">
          <Pagination
            current={safePage}
            pageSize={pageSize}
            total={visibleTotal}
            onChange={onPageChange}
            onPageSizeChange={(nextPageSize) => {
              onPageSizeChange(nextPageSize);
              onPageChange(1);
            }}
            pageSizeOptions={[10, 20, 50]}
            className="justify-end pt-4"
          />
        </div>
      </Card>
    </section>
  );
}
