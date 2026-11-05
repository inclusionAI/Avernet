import { Button } from '@/components/ui/Button';
import { Empty } from '@/components/ui/Empty';
import { Input } from '@/components/ui/Input';
import { Pagination } from '@/components/ui/Pagination';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/Select';
import { getRoutineScheduleLabel } from '@/services/botWorkshop/routineSchedule';
import { stageNames, type ManagedRoutine, type RoutineStage } from '@/services/botScheduledTasks/types';
import type { ReactNode } from 'react';
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
    <section className="rounded-xl border border-border bg-card p-5 sm:p-6">
      <div className="mb-5 flex flex-wrap items-center gap-3">
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
        <Input
          className="max-w-sm"
          aria-label="当前页筛选"
          placeholder="当前页筛选：名称、指令或实例"
          value={keyword}
          onChange={(event) => onKeyword(event.target.value)}
        />
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
              <tr>
                {['任务', '运行目标', '频率 / 时区', '下次执行（预计）', '调度状态', '操作'].map((title) => (
                  <th key={title} className="px-3 py-3 font-medium">
                    {title}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {visible.map((task) => (
                <tr
                  key={task.key}
                  className="cursor-pointer border-t border-border hover:bg-muted/40"
                  onClick={() => onSelect(task)}
                >
                  <td className="max-w-xs px-3 py-4">
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-auto p-0 text-left font-medium text-primary"
                      onClick={(event) => {
                        event.stopPropagation();
                        onSelect(task);
                      }}
                    >
                      {task.name}
                    </Button>
                    <p className="mb-0 mt-1 line-clamp-2 text-xs text-muted-foreground">{task.command}</p>
                  </td>
                  <td className="max-w-xs break-all px-3 py-4 text-xs">
                    {task.runtime_stage ? stageNames[task.runtime_stage] || task.runtime_stage : '运行态未报告'}
                    <p className="mt-1 text-muted-foreground">{task.device_uuid || '实例未报告'}</p>
                  </td>
                  <td className="px-3 py-4 text-xs">
                    {getRoutineScheduleLabel(task.cron)}
                    <p className="mt-1 text-muted-foreground">{task.timezone || '时区未报告'}</p>
                  </td>
                  <td className="whitespace-nowrap px-3 py-4 text-xs">
                    <RoutineNextRun task={task} now={now} />
                  </td>
                  <td className="px-3 py-4">{task.enabled ? '已启用' : '已停用'}</td>
                  <td className="px-3 py-4">{actions(task)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <Empty
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
        className="mt-5 justify-end"
      />
    </section>
  );
}
