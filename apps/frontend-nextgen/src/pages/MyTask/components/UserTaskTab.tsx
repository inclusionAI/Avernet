import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { Empty } from '@/components/ui/Empty';
import { Input } from '@/components/ui/Input';
import { Pagination } from '@/components/ui/Pagination';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/Select';
import { Spin } from '@/components/ui/Spin';
import type { TaskListItem } from '@/domain/tasks/models';
import { cn } from '@/utils/cn';
import { Search } from 'lucide-react';
import React, { useMemo, useState } from 'react';
import {
  formatDateTime,
  getBotDisplayName,
  getUserTaskGoal,
  getUserTaskTitle,
  sourceTypeBadge,
  taskStatusBadge,
  taskTypeBadge,
  type UserTaskStatusFilter,
  userTaskStatusOptions,
} from '../userTaskUtils';
import { UserTaskActions } from './UserTaskActions';
import { filterUserTaskRecords, type UserTaskOwnerBotOption } from './userTaskFilters';
import { UserTaskOwnerBotFilter } from './UserTaskOwnerBotFilter';
import {
  UserTaskGoalContent,
  UserTaskModifiedTime,
  UserTaskOwnerBotCell,
  UserTaskPrimaryContent,
} from './UserTaskTableCells';

function Th({ className, ...props }: React.ThHTMLAttributes<HTMLTableCellElement>) {
  return <th className={cn('h-10 px-4 py-3 text-xs font-medium text-muted-foreground', className)} {...props} />;
}

function Td({ className, ...props }: React.TdHTMLAttributes<HTMLTableCellElement>) {
  return <td className={cn('border-t border-divider px-4 py-3 align-top text-xs', className)} {...props} />;
}

export interface UserTaskTabProps {
  taskRecords: TaskListItem[];
  total: number;
  page: number;
  pageSize: number;
  loading: boolean;
  error: string | null;
  statusFilter: UserTaskStatusFilter;
  keyword: string;
  onKeywordChange: (keyword: string) => void;
  onStatusFilterChange: (status: UserTaskStatusFilter) => void;
  onRetry: () => void;
  onSelectTask: (taskId: string) => void;
  selectedTaskId: string | null;
  onPageChange: (page: number) => void;
  onPageSizeChange: (pageSize: number) => void;
  botNameMap: Record<string, string>;
  ownerBotUserIdMap: Record<string, string>;
  ownerBotMetaMap?: Record<string, { botId: string; avatarUrl?: string }>;
  ownerBotOptions?: UserTaskOwnerBotOption[];
  /** 实验室页专用列与操作布局；默认值保持“我的任务”现有行为。 */
  displayVariant?: 'default' | 'lab';
}

export function UserTaskTab({
  taskRecords,
  total,
  page,
  pageSize,
  loading,
  error,
  statusFilter,
  keyword,
  onKeywordChange,
  onStatusFilterChange,
  onRetry,
  onSelectTask,
  selectedTaskId,
  onPageChange,
  onPageSizeChange,
  botNameMap,
  ownerBotUserIdMap,
  ownerBotMetaMap = {},
  ownerBotOptions = [],
  displayVariant = 'default',
}: UserTaskTabProps) {
  const [ownerBotFilter, setOwnerBotFilter] = useState('all');
  const isLabVariant = displayVariant === 'lab';
  const columnCount = isLabVariant ? 7 : 6;

  const filteredTaskRecords = useMemo(
    () =>
      filterUserTaskRecords({
        taskRecords,
        ownerBotFilter,
        ownerBotOptions,
      }),
    [ownerBotFilter, ownerBotOptions, taskRecords],
  );

  return (
    <section className="space-y-4">
      <div className={cn('grid gap-3', isLabVariant ? 'md:grid-cols-[2fr_1.2fr_1.2fr]' : 'md:grid-cols-2')}>
        <div className="relative min-w-0">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={keyword}
            onChange={(event) => {
              onKeywordChange(event.target.value);
              onPageChange(1);
            }}
            placeholder={isLabVariant ? '搜索任务标题' : '搜索任务标题 / ID / Bot / 来源'}
            className="pl-9 text-xs"
          />
        </div>
        <div className="flex min-w-0 items-center gap-2">
          <span className="shrink-0 text-xs font-medium text-muted-foreground">状态</span>
          <Select value={statusFilter} onValueChange={(value) => onStatusFilterChange(value as UserTaskStatusFilter)}>
            <SelectTrigger aria-label="状态" className="w-full text-xs">
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
        {isLabVariant ? (
          <UserTaskOwnerBotFilter
            value={ownerBotFilter}
            options={ownerBotOptions}
            onChange={(value) => {
              setOwnerBotFilter(value);
              onPageChange(1);
            }}
          />
        ) : null}
      </div>

      <Card className={cn('overflow-hidden rounded-lg shadow-sm', isLabVariant && 'border-0 shadow-none')}>
        <div className="overflow-hidden bg-card">
          <div className="app-scrollbar overflow-x-auto">
            <table
              className={cn(
                'min-w-full border-separate border-spacing-0',
                isLabVariant &&
                  'w-full min-w-[1440px] table-fixed [&_td]:align-middle [&_td]:py-4 [&_th]:whitespace-nowrap [&_th]:text-foreground [&_th]:text-sm [&_td]:text-sm',
              )}
            >
              {isLabVariant ? (
                <colgroup>
                  {['18%', '19%', '17%', '13%', '13%', '7%', '13%'].map((width, index) => (
                    <col key={index} style={{ width }} />
                  ))}
                </colgroup>
              ) : null}
              <thead>
                <tr className="bg-muted/30 text-left text-xs font-medium text-muted-foreground">
                  <Th>任务</Th>
                  {isLabVariant ? <Th>任务目标</Th> : null}
                  <Th>{isLabVariant ? 'Owner Bot' : 'Owner Bot / 来源'}</Th>
                  {!isLabVariant ? <Th>状态</Th> : null}
                  <Th>创建时间</Th>
                  <Th>{isLabVariant ? '更新时间' : '完成时间'}</Th>
                  {isLabVariant ? <Th>状态</Th> : null}
                  <Th className={isLabVariant ? 'text-left' : 'text-right'}>操作</Th>
                </tr>
              </thead>
              <tbody>
                {loading ? (
                  <tr>
                    <td colSpan={columnCount} className="p-0">
                      <Spin tip="加载用户任务中…" className="py-16" />
                    </td>
                  </tr>
                ) : error ? (
                  <tr>
                    <td colSpan={columnCount} className="p-0">
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
                    <td colSpan={columnCount} className="p-0">
                      <Empty
                        title="暂无符合条件的用户任务"
                        description={
                          keyword || statusFilter !== 'all' || (isLabVariant && ownerBotFilter !== 'all')
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
                    const taskTitle = getUserTaskTitle(record);
                    const taskGoal = getUserTaskGoal(record);
                    return (
                      <tr
                        key={record.task_id}
                        className={cn(
                          'border-b border-divider text-xs transition-colors hover:bg-muted/50',
                          active && 'bg-secondary',
                        )}
                      >
                        <Td className="max-w-[22rem]">
                          <UserTaskPrimaryContent record={record} title={taskTitle} lab={isLabVariant} />
                        </Td>
                        {isLabVariant ? (
                          <Td className="max-w-[24rem]">
                            <UserTaskGoalContent goal={taskGoal} />
                          </Td>
                        ) : null}
                        <Td>
                          {isLabVariant ? (
                            <UserTaskOwnerBotCell
                              name={ownerBotName}
                              botId={
                                ownerBotMetaMap[record.owner_bot_id.trim().toLowerCase()]?.botId || record.owner_bot_id
                              }
                              avatarUrl={ownerBotMetaMap[record.owner_bot_id.trim().toLowerCase()]?.avatarUrl}
                            />
                          ) : (
                            <div className="space-y-1">
                              <div className="text-xs leading-5 text-foreground">
                                <span className="font-medium">{ownerBotName}</span>
                              </div>
                              <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                                {sourceTypeBadge(record.source_type ?? '—')}
                                {taskTypeBadge(record.execution_config?.task_type ?? '—')}
                              </div>
                            </div>
                          )}
                        </Td>
                        {!isLabVariant ? <Td className="whitespace-nowrap">{taskStatusBadge(record.status)}</Td> : null}
                        <Td className={isLabVariant ? 'whitespace-nowrap' : undefined}>
                          <div className="text-xs leading-5 text-foreground">{formatDateTime(record.gmt_create)}</div>
                        </Td>
                        <Td>
                          <UserTaskModifiedTime record={record} lab={isLabVariant} />
                        </Td>
                        {isLabVariant ? (
                          <Td className="whitespace-nowrap">{taskStatusBadge(record.status, isLabVariant)}</Td>
                        ) : null}
                        <Td>
                          <UserTaskActions
                            record={record}
                            lab={isLabVariant}
                            ownerBotUserIdMap={ownerBotUserIdMap}
                            onSelect={onSelectTask}
                          />
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
            layout={isLabVariant ? 'between' : 'end'}
            className="pt-4"
          />
        </div>
      </Card>
    </section>
  );
}
