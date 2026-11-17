import { Button } from '@/components/ui/Button';
import { Empty } from '@/components/ui/Empty';
import { Input } from '@/components/ui/Input';
import { Pagination } from '@/components/ui/Pagination';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/Select';
import { stageNames, type ManagedRoutine, type RoutineStage } from '@/services/botScheduledTasks/types';
import { getRoutineScheduleLabel } from '@/services/botWorkshop/routineSchedule';
import { Search } from 'lucide-react';
import type { ReactNode } from 'react';
import { EnableBadge } from './RoutineBadges';
import { RoutineNextRun, useRoutineClock } from './RoutineNextRun';
export function RoutineList({
  tasks,
  total,
  page,
  pageSize,
  stage,
  showStageFilter,
  keyword,
  canManage,
  editable,
  busy,
  incomplete,
  onKeyword,
  onStage,
  onPage,
  onPageSize,
  onCreate,
  onSelect,
  actions,
}: {
  tasks: ManagedRoutine[];
  total: number;
  page: number;
  pageSize: number;
  stage?: RoutineStage;
  showStageFilter: boolean;
  keyword: string;
  canManage: boolean;
  editable: boolean;
  busy: boolean;
  incomplete: boolean;
  onKeyword: (value: string) => void;
  onStage: (value: string) => void;
  onPage: (value: number) => void;
  onPageSize: (value: number) => void;
  onCreate: () => void;
  onSelect: (task: ManagedRoutine) => void;
  actions: (task: ManagedRoutine) => ReactNode;
}) {
  const now = useRoutineClock();
  const visible = tasks.filter((task) =>
    `${task.name} ${task.command} ${task.cron} ${task.device_uuid || ''}`
      .toLowerCase()
      .includes(keyword.trim().toLowerCase()),
  );
  return (
    <section className="overflow-hidden rounded-xl border border-border bg-card">
      <div className="flex flex-wrap items-center gap-3 border-b border-divider px-5 py-4 sm:px-6">
        {showStageFilter && (
          <Select value={stage || 'all'} onValueChange={onStage}>
            <SelectTrigger className="w-40" aria-label="运行态筛选">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">全部运行态</SelectItem>
              {(['draft', 'verify', 'online'] as const).map((value) => (
                <SelectItem key={value} value={value}>
                  {stageNames[value]}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
        <div className="relative max-w-sm flex-1">
          <Search
            aria-hidden
            className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground"
          />
          <Input
            className="pl-8"
            aria-label="当前页筛选"
            placeholder="筛选当前页：名称 / 指令 / 实例"
            value={keyword}
            onChange={(event) => onKeyword(event.target.value)}
          />
        </div>
        {canManage && (
          <Button className="ml-auto" disabled={!editable || busy} onClick={onCreate}>
            创建定时任务
          </Button>
        )}
      </div>
      {visible.length ? (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="text-xs text-muted-foreground">
              <tr className="[&_th]:border-b [&_th]:border-divider [&_th]:font-medium">
                {['任务', '运行目标', '频率 / 时区', '下次执行（预计）', '调度状态', '操作'].map((title) => (
                  <th key={title} className="px-4 py-2.5">
                    {title}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {visible.map((task) => (
                <tr
                  key={task.key}
                  className="cursor-pointer border-t border-border/70 transition-colors hover:bg-muted/40"
                  onClick={() => onSelect(task)}
                >
                  <td className="max-w-xs px-4 py-3.5">
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-auto px-0 py-0 text-left text-sm font-medium text-primary hover:bg-transparent"
                      onClick={(event) => {
                        event.stopPropagation();
                        onSelect(task);
                      }}
                    >
                      {task.name}
                    </Button>
                    {task.command && (
                      <p className="mb-0 mt-1 line-clamp-1 break-all font-mono text-xs text-muted-foreground">
                        {task.command}
                      </p>
                    )}
                  </td>
                  <td className="max-w-xs px-4 py-3.5 text-xs">
                    {task.runtime_stage ? (
                      <span>{stageNames[task.runtime_stage] || task.runtime_stage}</span>
                    ) : (
                      <span className="text-muted-foreground">运行态未报告</span>
                    )}
                    <p className="mb-0 mt-1 break-all font-mono text-muted-foreground">
                      {task.device_uuid || '实例未报告'}
                    </p>
                  </td>
                  <td className="px-4 py-3.5 text-xs">
                    {getRoutineScheduleLabel(task.cron)}
                    <p className="mb-0 mt-1 font-mono text-muted-foreground">
                      {task.cron}
                      {task.timezone ? ` · ${task.timezone}` : ''}
                    </p>
                  </td>
                  <td className="whitespace-nowrap px-4 py-3.5 text-xs">
                    <RoutineNextRun task={task} now={now} />
                  </td>
                  <td className="px-4 py-3.5">
                    <EnableBadge enabled={task.enabled} />
                  </td>
                  <td className="px-4 py-3.5">{actions(task)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <Empty
          className="py-14"
          title={
            keyword ? '当前页没有匹配任务' : incomplete ? '未获取到任务，存在查询失败的实例' : '当前范围暂无定时任务'
          }
          description={
            incomplete ? '请先处理查询失败的目标，再刷新确认。' : '创建任务后，可在这里管理配置并查看历史执行实例。'
          }
        />
      )}
      <Pagination
        current={page}
        pageSize={pageSize}
        total={total}
        onChange={onPage}
        onPageSizeChange={onPageSize}
        pageSizeOptions={[10, 20, 50]}
        className="border-t border-divider px-5 py-4 sm:px-6"
      />
    </section>
  );
}
