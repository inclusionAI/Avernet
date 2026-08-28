import { Button } from '@/components/ui/Button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/Card';
import { Empty } from '@/components/ui/Empty';
import { Input } from '@/components/ui/Input';
import { Segmented } from '@/components/ui/Segmented';
import { Spin } from '@/components/ui/Spin';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';
import type { TaskListItem } from '@/domain/tasks/models';
import { cn } from '@/utils/cn';
import { Eye, Search } from 'lucide-react';
import React, { useMemo, useState } from 'react';
import {
  formatDateTime,
  formatDuration,
  getUserTaskDescription,
  getUserTaskTitle,
  matchUserTaskScope,
  sourceTypeBadge,
  taskStatusBadge,
  taskTypeBadge,
  type UserTabFilter,
  userTabOptions,
} from '../userTaskUtils';

function Th({ className, ...props }: React.ThHTMLAttributes<HTMLTableCellElement>) {
  return <th className={cn('px-4 py-3 text-xs font-medium', className)} {...props} />;
}

function Td({ className, ...props }: React.TdHTMLAttributes<HTMLTableCellElement>) {
  return <td className={cn('border-t border-[var(--color-border)] px-4 py-4 align-top', className)} {...props} />;
}

function formatTaskTitle(title: string): string {
  const chars = Array.from(title);
  return chars.length > 8 ? `${chars.slice(0, 8).join('')}...` : title;
}

export interface UserTaskTabProps {
  taskRecords: TaskListItem[];
  loading: boolean;
  error: string | null;
  onRetry: () => void;
  onSelectTask: (taskId: string) => void;
  selectedTaskId: string | null;
}

export function UserTaskTab({ taskRecords, loading, error, onRetry, onSelectTask, selectedTaskId }: UserTaskTabProps) {
  const [userFilter, setUserFilter] = useState<UserTabFilter>('all');
  const [userKeyword, setUserKeyword] = useState('');

  const filteredTaskRecords = useMemo(() => {
    const keyword = userKeyword.trim().toLowerCase();
    return taskRecords.filter((record) => {
      const title = getUserTaskTitle(record).toLowerCase();
      const taskId = record.task_id.toLowerCase();
      const botId = (record.owner_bot_id ?? '').toLowerCase();
      const sourceType = (record.source_type ?? '').toLowerCase();
      const matchesKeyword =
        !keyword ||
        title.includes(keyword) ||
        taskId.includes(keyword) ||
        botId.includes(keyword) ||
        sourceType.includes(keyword);
      return matchesKeyword && matchUserTaskScope(record.status, userFilter);
    });
  }, [taskRecords, userKeyword, userFilter]);

  return (
    <section className="space-y-4">
      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <CardTitle>用户任务</CardTitle>
              <CardDescription>当前账号下由用户发起的任务列表，支持按状态筛选并查看详情抽屉。</CardDescription>
            </div>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex flex-col gap-3 xl:flex-row xl:items-center xl:justify-between">
            <div className="flex items-center gap-2 xl:w-[26rem]">
              <div className="relative flex-1">
                <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-[var(--color-muted)]" />
                <Input
                  value={userKeyword}
                  onChange={(event) => setUserKeyword(event.target.value)}
                  placeholder="搜索任务标题 / ID / Bot / 来源"
                  className="pl-9"
                />
              </div>
            </div>
            <Segmented
              value={userFilter}
              onChange={(value) => setUserFilter(value as UserTabFilter)}
              options={userTabOptions.map((item) => ({ value: item.value, label: item.label }))}
              className="w-full xl:max-w-2xl"
            />
          </div>
        </CardContent>
      </Card>

      <Card>
        <div className="overflow-hidden">
          <div className="app-scrollbar overflow-x-auto">
            <table className="min-w-full border-separate border-spacing-0">
              <thead>
                <tr className="bg-[var(--color-panel-strong)]/60 text-left text-xs font-medium text-[var(--color-muted)]">
                  <Th className="rounded-tl-xl">任务</Th>
                  <Th>Owner Bot / 来源</Th>
                  <Th>类型</Th>
                  <Th>状态</Th>
                  <Th>创建时间</Th>
                  <Th>完成时间</Th>
                  <Th className="rounded-tr-xl" style={{ textAlign: 'center' }}>
                    操作
                  </Th>
                </tr>
              </thead>
              <tbody>
                {loading ? (
                  <tr>
                    <td colSpan={7} className="p-0">
                      <Spin tip="加载用户任务中…" className="py-16" />
                    </td>
                  </tr>
                ) : error ? (
                  <tr>
                    <td colSpan={7} className="p-0">
                      <Empty
                        title="用户任务加载失败"
                        description={error}
                        action={
                          <Button variant="secondary" onClick={onRetry}>
                            重试
                          </Button>
                        }
                      />
                    </td>
                  </tr>
                ) : filteredTaskRecords.length === 0 ? (
                  <tr>
                    <td colSpan={7} className="p-0">
                      <Empty
                        title="暂无符合条件的用户任务"
                        description={
                          userKeyword || userFilter !== 'all'
                            ? '请尝试清除筛选条件或刷新数据。'
                            : '当前账号下还没有任务记录。'
                        }
                      />
                    </td>
                  </tr>
                ) : (
                  filteredTaskRecords.map((record) => {
                    const active = record.task_id === selectedTaskId;
                    return (
                      <tr
                        key={record.task_id}
                        className={cn(
                          'border-b border-[var(--color-border)] text-xs transition-colors hover:bg-[var(--color-panel-muted)]/60',
                          active && 'bg-[var(--color-primary-soft)]/35',
                        )}
                      >
                        <Td className="max-w-[22rem]">
                          <div className="space-y-1">
                            <div className="flex min-w-0 items-center gap-2">
                              <TooltipProvider delayDuration={300}>
                                <Tooltip>
                                  <TooltipTrigger asChild>
                                    <span className="shrink-0 font-medium text-[var(--color-fg)]">
                                      {formatTaskTitle(getUserTaskTitle(record))}
                                    </span>
                                  </TooltipTrigger>
                                  <TooltipContent>{getUserTaskTitle(record)}</TooltipContent>
                                </Tooltip>
                              </TooltipProvider>
                              <code className="min-w-0 truncate rounded bg-[var(--color-panel-strong)] px-1.5 py-0.5 text-xs text-[var(--color-muted)]">
                                {record.task_id}
                              </code>
                            </div>
                            <p className="m-0 line-clamp-2 text-xs leading-5 text-[var(--color-muted)]">
                              {getUserTaskDescription(record)}
                            </p>
                            {record.task_spec?.goal?.objective ? (
                              <div className="line-clamp-1 text-xs text-[var(--color-muted)]">
                                目标：{record.task_spec.goal.objective}
                              </div>
                            ) : null}
                          </div>
                        </Td>
                        <Td>
                          <div className="space-y-1">
                            <div className="text-[var(--color-fg)]">
                              <span className="font-medium">{record.owner_bot_id ?? '—'}</span>
                            </div>
                            <div className="flex flex-wrap items-center gap-2 text-xs text-[var(--color-muted)]">
                              {sourceTypeBadge(record.source_type ?? '—')}
                            </div>
                          </div>
                        </Td>
                        <Td>
                          <div className="space-y-2">
                            {taskTypeBadge(record.execution_config?.task_type ?? '—')}
                            <div className="text-xs text-[var(--color-muted)]">来源：{record.source_type ?? '—'}</div>
                          </div>
                        </Td>
                        <Td className="whitespace-nowrap">{taskStatusBadge(record.status)}</Td>
                        <Td>
                          <div className="text-xs text-[var(--color-fg)]">{formatDateTime(record.gmt_create)}</div>
                        </Td>
                        <Td>
                          <div className="space-y-1 text-xs text-[var(--color-fg)]">
                            <div>{formatDateTime(record.gmt_modified)}</div>
                            <div className="text-xs text-[var(--color-muted)]">
                              耗时：{formatDuration(record.gmt_create, record.gmt_modified)}
                            </div>
                          </div>
                        </Td>
                        <Td>
                          <div className="flex justify-center gap-2" onClick={(event) => event.stopPropagation()}>
                            <Button
                              variant="secondary"
                              size="sm"
                              leftIcon={<Eye className="size-4" />}
                              onClick={() => onSelectTask(record.task_id)}
                            >
                              查看详情
                            </Button>
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
      </Card>
    </section>
  );
}
