import { Button } from '@/components/ui/Button';
import {
  Drawer,
  DrawerContent,
  DrawerDescription,
  DrawerFooter,
  DrawerHeader,
  DrawerTitle,
} from '@/components/ui/Drawer';
import { Empty } from '@/components/ui/Empty';
import type { ScheduledRoutineRecord, ScheduledRoutineRunRecord } from '@/services/scheduledTasks';
import { cn } from '@/utils/cn';
import { Bot, CalendarDays, Clock3, History, ListTodo } from 'lucide-react';
import React from 'react';

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

function InfoCell({
  label,
  value,
  icon,
  className,
}: {
  label: string;
  value: React.ReactNode;
  icon: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn('space-y-1', className)}>
      <div className="flex items-center gap-1.5 text-xs font-medium text-[var(--color-muted)]">
        <span className="flex size-5 items-center justify-center rounded-md bg-white text-[var(--color-primary)]">
          {icon}
        </span>
        <span>{label}</span>
      </div>
      <div className="break-words text-sm text-[var(--color-fg)]">{value || '—'}</div>
    </div>
  );
}

export interface MyTaskRoutineDrawerProps {
  selectedRoutine: ScheduledRoutineRecord | null;
  selectedRoutineKey: string | null;
  onCloseRoutine: () => void;
  selectedRoutineRuns: ScheduledRoutineRunRecord[];
  selectedRoutineRunsLoading: boolean;
  selectedRoutineRunsError: string | null;
}

export function MyTaskRoutineDrawer({
  selectedRoutine,
  selectedRoutineKey,
  onCloseRoutine,
  selectedRoutineRuns,
  selectedRoutineRunsLoading,
  selectedRoutineRunsError,
}: MyTaskRoutineDrawerProps) {
  return (
    <Drawer open={Boolean(selectedRoutineKey)} onOpenChange={(open) => !open && onCloseRoutine()}>
      <DrawerContent side="right" size="lg">
        <DrawerHeader>
          <div className="flex items-start justify-between gap-3 pr-8">
            <div>
              <DrawerTitle className="text-xl font-semibold text-[var(--color-fg)]">
                {selectedRoutine?.name ?? '定时任务实例'}
              </DrawerTitle>
              <DrawerDescription className="mt-2 text-sm text-[var(--color-muted)]">
                定时任务详情和实例列表均由后端 routines 接口返回。
              </DrawerDescription>
            </div>
          </div>
        </DrawerHeader>
        {selectedRoutine ? (
          <div className="space-y-6">
            <section className="grid gap-3 rounded-xl border border-[var(--color-border)] bg-[var(--color-panel-muted)]/40 p-4 text-sm sm:grid-cols-2">
              <InfoCell
                icon={<Bot className="size-4" />}
                label="Owner Bot"
                value={
                  <div className="space-y-1">
                    <div>{selectedRoutine.botName}</div>
                    <div className="text-xs text-[var(--color-muted)]">Bot ID：{selectedRoutine.botId}</div>
                  </div>
                }
              />
              <InfoCell
                icon={<Clock3 className="size-4" />}
                label="频率 / 时区"
                value={`${selectedRoutine.frequency} · ${selectedRoutine.timezone ?? '—'}`}
              />
              <InfoCell
                icon={<CalendarDays className="size-4" />}
                label="下次执行"
                value={formatDateTime(selectedRoutine.nextRunAt)}
              />
              <InfoCell
                icon={<History className="size-4" />}
                label="最近执行"
                value={formatDateTime(selectedRoutine.lastRunAt)}
              />
              <InfoCell
                icon={<ListTodo className="size-4" />}
                label="提示词摘要"
                value={selectedRoutine.prompt}
                className="sm:col-span-2"
              />
            </section>

            <section className="space-y-3">
              <h3 className="m-0 text-sm font-semibold text-[var(--color-fg)]">最近实例</h3>
              {selectedRoutineRunsLoading ? (
                <div className="rounded-xl border border-[var(--color-border)] p-6 text-center text-sm text-[var(--color-muted)]">
                  定时任务实例加载中…
                </div>
              ) : selectedRoutineRunsError ? (
                <Empty title="定时任务实例加载失败" description={selectedRoutineRunsError} />
              ) : (
                <div className="space-y-3">
                  {selectedRoutineRuns.length > 0 ? (
                    selectedRoutineRuns.map((item) => (
                      <div
                        key={item.id}
                        className="rounded-xl border border-[var(--color-border)] bg-white p-4 shadow-sm transition-shadow hover:shadow-md"
                      >
                        <div className="flex flex-wrap items-center justify-between gap-2">
                          <span className="text-sm font-medium text-[var(--color-fg)]">{item.instanceNo}</span>
                          <span className="text-xs text-[var(--color-muted)]">
                            计划触发：{formatDateTime(item.plannedTriggerAt)}
                          </span>
                        </div>
                        <div className="mt-3 grid gap-2 text-sm text-[var(--color-muted)] sm:grid-cols-2">
                          <InfoCell
                            icon={<CalendarDays className="size-4" />}
                            label="实际触发"
                            value={formatDateTime(item.actualTriggerAt)}
                          />
                          <InfoCell icon={<Clock3 className="size-4" />} label="耗时" value={item.duration} />
                          <InfoCell
                            icon={<ListTodo className="size-4" />}
                            label="关联任务"
                            value={item.taskName ?? '—'}
                            className="sm:col-span-2"
                          />
                        </div>
                        {item.outputSummary ? (
                          <p className="mt-3 line-clamp-2 text-sm text-[var(--color-muted)]">{item.outputSummary}</p>
                        ) : null}
                        {item.errorMessage ? (
                          <p className="mt-3 rounded-lg bg-[var(--color-error-soft)] px-3 py-2 text-sm text-[var(--color-error)]">
                            {item.errorMessage}
                          </p>
                        ) : null}
                      </div>
                    ))
                  ) : (
                    <Empty title="暂无最近实例" description="当前定时任务还没有实例记录。" />
                  )}
                </div>
              )}
            </section>
          </div>
        ) : (
          <Empty title="未找到定时任务" description="请从定时任务列表重新打开实例抽屉。" />
        )}
        <DrawerFooter className="justify-end">
          <Button onClick={onCloseRoutine}>关闭</Button>
        </DrawerFooter>
      </DrawerContent>
    </Drawer>
  );
}
