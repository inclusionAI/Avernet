import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import {
  Drawer,
  DrawerContent,
  DrawerDescription,
  DrawerFooter,
  DrawerHeader,
  DrawerTitle,
} from '@/components/ui/Drawer';
import { Empty } from '@/components/ui/Empty';
import type { ScheduledRoutineRunRecord } from '@/services/scheduledTasks';
import { cn } from '@/utils/cn';
import React from 'react';

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

export interface MyTaskRoutineHistoryDrawerProps {
  selectedRoutineHistoryId: string | null;
  onCloseRoutineHistory: () => void;
  historyRuns: ScheduledRoutineRunRecord[];
  historyLoading: boolean;
  historyError: string | null;
  onOpenRoutineFromHistory: (botId: string, routineId: string) => void;
}

export function MyTaskRoutineHistoryDrawer({
  selectedRoutineHistoryId,
  onCloseRoutineHistory,
  historyRuns,
  historyLoading,
  historyError,
  onOpenRoutineFromHistory,
}: MyTaskRoutineHistoryDrawerProps) {
  return (
    <Drawer open={Boolean(selectedRoutineHistoryId)} onOpenChange={(open) => !open && onCloseRoutineHistory()}>
      <DrawerContent side="right" size="lg" className="w-[min(100vw,60rem)]">
        <DrawerHeader>
          <DrawerTitle className="text-xl font-semibold text-[var(--color-fg)]">历史任务</DrawerTitle>
          <DrawerDescription className="mt-2 text-sm text-[var(--color-muted)]">
            查看所有定时任务的历史执行记录。每条历史任务都可继续查看对应实例，便于定位单次运行详情。
          </DrawerDescription>
        </DrawerHeader>
        {historyError && historyRuns.length > 0 ? (
          <Card className="mb-3 border-warning/20 bg-warning/10 px-3 py-2 text-sm text-warning shadow-none">
            {historyError}
          </Card>
        ) : null}
        <div className="overflow-hidden rounded-xl border border-[var(--color-border)]">
          <div className="app-scrollbar overflow-x-auto">
            <table className="min-w-full border-separate border-spacing-0">
              <thead>
                <tr className="bg-[var(--color-panel-strong)]/60 text-left text-xs font-medium text-[var(--color-muted)]">
                  <Th className="rounded-tl-xl">历史任务</Th>
                  <Th>计划触发</Th>
                  <Th>实际触发</Th>
                  <Th>耗时</Th>
                  <Th>关联任务</Th>
                  <Th className="rounded-tr-xl text-right">操作</Th>
                </tr>
              </thead>
              <tbody>
                {historyLoading ? (
                  <tr>
                    <td colSpan={6} className="p-0">
                      <div className="py-16 text-center text-sm text-[var(--color-muted)]">历史任务加载中…</div>
                    </td>
                  </tr>
                ) : historyError && historyRuns.length === 0 ? (
                  <tr>
                    <td colSpan={6} className="p-0">
                      <Empty title="历史任务加载失败" description={historyError} />
                    </td>
                  </tr>
                ) : historyRuns.length > 0 ? (
                  historyRuns.map((item) => (
                    <tr
                      key={item.id}
                      className="border-b border-[var(--color-border)] text-xs transition-colors hover:bg-[var(--color-panel-muted)]/60"
                    >
                      <Td className="max-w-[18rem]">
                        <div className="space-y-1">
                          <div className="truncate font-medium text-[var(--color-fg)]">{item.routineName}</div>
                          <div className="flex flex-wrap items-center gap-2 text-xs text-[var(--color-muted)]">
                            <span>{item.botName ?? item.botId}</span>
                            <code className="rounded bg-[var(--color-panel-strong)] px-1.5 py-0.5">
                              {item.instanceNo}
                            </code>
                          </div>
                          <p className="m-0 line-clamp-2 text-xs leading-5 text-[var(--color-muted)]">
                            {item.outputSummary ?? item.errorMessage ?? '历史任务执行记录'}
                          </p>
                        </div>
                      </Td>
                      <Td>
                        <div className="text-xs text-[var(--color-fg)]">{formatDateTime(item.plannedTriggerAt)}</div>
                      </Td>
                      <Td>
                        <div className="text-xs text-[var(--color-fg)]">{formatDateTime(item.actualTriggerAt)}</div>
                      </Td>
                      <Td>
                        <div className="text-xs text-[var(--color-fg)]">{item.duration ?? '—'}</div>
                      </Td>
                      <Td>
                        <div className="text-xs text-[var(--color-fg)]">{item.taskName ?? '—'}</div>
                      </Td>
                      <Td>
                        <div className="flex justify-end">
                          <Button
                            variant="secondary"
                            size="sm"
                            onClick={(event) => {
                              event.stopPropagation();
                              onOpenRoutineFromHistory(item.botId, item.routineId);
                            }}
                          >
                            查看实例
                          </Button>
                        </div>
                      </Td>
                    </tr>
                  ))
                ) : (
                  <tr>
                    <td colSpan={6} className="p-0">
                      <Empty title="暂无历史任务" description="当前还没有可查看的执行历史。" />
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
        <DrawerFooter>
          <Button onClick={onCloseRoutineHistory}>关闭</Button>
        </DrawerFooter>
      </DrawerContent>
    </Drawer>
  );
}
