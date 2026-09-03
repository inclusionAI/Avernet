import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { Empty } from '@/components/ui/Empty';
import { Input } from '@/components/ui/Input';
import { Pagination } from '@/components/ui/Pagination';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/Select';
import { Spin } from '@/components/ui/Spin';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';
import type { TaskListItem } from '@/domain/tasks/models';
import { cn } from '@/utils/cn';
import { Eye, Search } from 'lucide-react';
import React, { useMemo, useState } from 'react';
import {
  formatDateTime,
  formatDuration,
  getBotDisplayName,
  getUserTaskTitle,
  sourceTypeBadge,
  taskStatusBadge,
  taskTypeBadge,
  type UserTaskStatusFilter,
  userTaskStatusOptions,
} from '../userTaskUtils';
import { UserTaskGoalMeta } from './UserTaskGoalMeta';
import { ViewSessionButton } from './ViewSessionButton';

function Th({ className, ...props }: React.ThHTMLAttributes<HTMLTableCellElement>) {
  return <th className={cn('h-10 px-4 py-3 text-xs font-medium text-muted-foreground', className)} {...props} />;
}

function Td({ className, ...props }: React.TdHTMLAttributes<HTMLTableCellElement>) {
  return <td className={cn('border-t border-border px-4 py-3 align-top text-xs', className)} {...props} />;
}

function formatTaskTitle(title: string): string {
  const chars = Array.from(title);
  return chars.length > 8 ? `${chars.slice(0, 8).join('')}...` : title;
}

export interface UserTaskTabProps {
  taskRecords: TaskListItem[];
  total: number;
  page: number;
  pageSize: number;
  loading: boolean;
  error: string | null;
  statusFilter: UserTaskStatusFilter;
  onStatusFilterChange: (status: UserTaskStatusFilter) => void;
  onRetry: () => void;
  onSelectTask: (taskId: string) => void;
  selectedTaskId: string | null;
  onPageChange: (page: number) => void;
  onPageSizeChange: (pageSize: number) => void;
  botNameMap: Record<string, string>;
}

export function UserTaskTab({
  taskRecords,
  total,
  page,
  pageSize,
  loading,
  error,
  statusFilter,
  onStatusFilterChange,
  onRetry,
  onSelectTask,
  selectedTaskId,
  onPageChange,
  onPageSizeChange,
  botNameMap,
}: UserTaskTabProps) {
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
      // 状态过滤已下沉到服务端(status 推送查询),这里只做关键字本地过滤。
      return matchesKeyword;
    });
  }, [taskRecords, userKeyword]);

  return (
    <section className="space-y-4">
      <div className="grid gap-3 border-y border-border py-3 md:grid-cols-3">
        <div className="relative min-w-0">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={userKeyword}
            onChange={(event) => {
              setUserKeyword(event.target.value);
              onPageChange(1);
            }}
            placeholder="搜索任务标题 / ID / Bot / 来源"
            className="pl-9 text-xs"
          />
        </div>
        <div className="flex min-w-0 items-center gap-2">
          <span className="shrink-0 text-xs font-medium text-muted-foreground">状态</span>
          <Select value={statusFilter} onValueChange={(value) => onStatusFilterChange(value as UserTaskStatusFilter)}>
            <SelectTrigger className="w-full text-xs">
              <SelectValue placeholder="请选择状态" />
            </SelectTrigger>
            <SelectContent>
              {userTaskStatusOptions.map((item) => (
                <SelectItem key={item.value} value={item.value}>
                  {item.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>

      <Card className="overflow-hidden rounded-lg shadow-sm">
        <div className="overflow-hidden bg-card">
          <div className="app-scrollbar overflow-x-auto">
            <table className="min-w-full border-separate border-spacing-0">
              <thead>
                <tr className="bg-muted/30 text-left text-xs font-medium text-muted-foreground">
                  <Th>任务</Th>
                  <Th>Owner Bot / 来源</Th>
                  <Th>类型</Th>
                  <Th>状态</Th>
                  <Th>创建时间</Th>
                  <Th>完成时间</Th>
                  <Th className="text-center">操作</Th>
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
                          userKeyword || statusFilter !== 'all'
                            ? '请尝试清除筛选条件或刷新数据。'
                            : '当前账号下还没有任务记录。'
                        }
                      />
                    </td>
                  </tr>
                ) : (
                  filteredTaskRecords.map((record) => {
                    const active = record.task_id === selectedTaskId;
                    const ownerBotName = getBotDisplayName(botNameMap, record.owner_bot_id);
                    return (
                      <tr
                        key={record.task_id}
                        className={cn(
                          'border-b border-border text-xs transition-colors hover:bg-muted/50',
                          active && 'bg-secondary',
                        )}
                      >
                        <Td className="max-w-[22rem]">
                          <div className="space-y-1">
                            <div className="flex min-w-0 items-center gap-2">
                              <TooltipProvider delayDuration={300}>
                                <Tooltip>
                                  <TooltipTrigger asChild>
                                    <span className="shrink-0 font-medium text-foreground">
                                      {formatTaskTitle(getUserTaskTitle(record))}
                                    </span>
                                  </TooltipTrigger>
                                  <TooltipContent>{getUserTaskTitle(record)}</TooltipContent>
                                </Tooltip>
                              </TooltipProvider>
                              <code className="min-w-0 truncate rounded bg-muted px-1.5 py-0.5 font-mono text-xs text-muted-foreground">
                                {record.task_id}
                              </code>
                            </div>
                            <UserTaskGoalMeta record={record} />
                          </div>
                        </Td>
                        <Td className="text-center">
                          <div className="space-y-1">
                            <div className="text-center text-foreground">
                              <span className="font-medium">{ownerBotName}</span>
                            </div>
                            <div className="flex flex-wrap items-center justify-center gap-2 text-xs text-muted-foreground">
                              {sourceTypeBadge(record.source_type ?? '—')}
                            </div>
                          </div>
                        </Td>
                        <Td>
                          <div className="space-y-2">{taskTypeBadge(record.execution_config?.task_type ?? '—')}</div>
                        </Td>
                        <Td className="whitespace-nowrap">{taskStatusBadge(record.status)}</Td>
                        <Td>
                          <div className="text-xs text-foreground">{formatDateTime(record.gmt_create)}</div>
                        </Td>
                        <Td>
                          <div className="space-y-1 text-xs text-foreground">
                            <div>{formatDateTime(record.gmt_modified)}</div>
                            <div className="text-xs text-muted-foreground">
                              耗时：{formatDuration(record.gmt_create, record.gmt_modified)}
                            </div>
                          </div>
                        </Td>
                        <Td>
                          <div
                            className="flex flex-col items-center justify-center gap-2"
                            onClick={(event) => event.stopPropagation()}
                          >
                            <ViewSessionButton record={record} />
                            <Button
                              variant="secondary"
                              size="sm"
                              leftIcon={<Eye className="size-4" />}
                              onClick={(event) => {
                                event.stopPropagation();
                                onSelectTask(record.task_id);
                              }}
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
        <div className="px-4 pb-4">
          <Pagination
            current={page}
            pageSize={pageSize}
            total={total}
            onChange={onPageChange}
            onPageSizeChange={onPageSizeChange}
            pageSizeOptions={[10, 20, 50]}
            className="justify-end pt-4"
          />
        </div>
      </Card>
    </section>
  );
}
