import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { DataTable, type DataTableColumn } from '@/components/ui/DataTable';
import { Empty } from '@/components/ui/Empty';
import { Pagination } from '@/components/ui/Pagination';
import { recoveryOriginalSessionPath } from '@/domain/recovery/originalSession';
import {
  recoveryEffectLabels,
  recoveryEffectTones,
  recoveryListStatus,
  recoveryStatusLabels,
  recoveryTime,
} from '@/domain/recovery/presentation';
import type { RecoveryListItem, RecoveryStatus } from '@/domain/recovery/types';
import { recoveryService } from '@/services/recovery/recoveryService';
import { ArrowUpRight, ChevronRight, RefreshCw } from 'lucide-react';
import { useCallback, useState } from 'react';
import { ErrorBox, FilterSelect, Hint, ListSkeleton, TablePanel, useResource } from '../shared';
import { RecoveryBotFilter, RecoverySettings } from './RecoveryBotOptions';

const pageSizes = [10, 20, 50];
const statuses = Object.entries(recoveryStatusLabels).map(([value, label]) => ({ value, label }));
const listWidths = ['w-[140px]', 'w-[160px]', 'w-[108px]', 'w-[88px]', 'w-[198px]', ''];

function StatusBadge({ item }: { item: RecoveryListItem }) {
  const { label, tone } = recoveryListStatus(item);
  return (
    <Badge tone={tone} className="whitespace-nowrap">
      {label}
    </Badge>
  );
}

function EffectBadge({ item }: { item: RecoveryListItem }) {
  return (
    <Badge tone={recoveryEffectTones[item.effect] ?? 'warning'} className="whitespace-nowrap">
      {recoveryEffectLabels[item.effect] ?? '尚未确认'}
    </Badge>
  );
}

function BotCell({ item }: { item: RecoveryListItem }) {
  return (
    <>
      <Hint content={item.botId}>
        <strong className="block truncate text-sm font-medium">{item.botId}</strong>
      </Hint>
      <div className="truncate text-xs text-muted-foreground">
        {item.entityId} · {item.env}
      </div>
    </>
  );
}

export function OriginalSessionLink({
  botId,
  ownerId,
  sessionKey,
}: {
  botId: string;
  ownerId: string;
  sessionKey: string;
}) {
  const path = recoveryOriginalSessionPath({ botId, ownerId, sessionKey });
  if (!path) return null;
  return (
    <a
      href={path}
      target="_blank"
      rel="noopener noreferrer"
      className="inline-flex items-center gap-1 whitespace-nowrap text-xs text-primary hover:underline"
      onClick={(event) => event.stopPropagation()}
      onKeyDown={(event) => event.stopPropagation()}
    >
      查看原会话 <ArrowUpRight size={12} aria-hidden />
    </a>
  );
}

function SessionCell({ item }: { item: RecoveryListItem }) {
  return (
    <>
      <Hint content={item.sessionKey}>
        <div className="truncate font-mono text-xs text-muted-foreground">{item.sessionKey || '—'}</div>
      </Hint>
      <OriginalSessionLink botId={item.botId} ownerId={item.entityId} sessionKey={item.sessionKey} />
    </>
  );
}

function TimeCell({ item }: { item: RecoveryListItem }) {
  return (
    <div className="space-y-1 whitespace-nowrap text-[13px] tabular-nums text-muted-foreground">
      <div>开始：{recoveryTime(item.startAtMs)}</div>
      <div>结束：{recoveryTime(item.endAtMs)}</div>
    </div>
  );
}

const columns: DataTableColumn<RecoveryListItem>[] = [
  { id: 'bot', header: 'Bot ID', width: listWidths[0], cell: (item) => <BotCell item={item} /> },
  { id: 'session', header: '会话标识', width: listWidths[1], cell: (item) => <SessionCell item={item} /> },
  { id: 'status', header: '任务状态', width: listWidths[2], cell: (item) => <StatusBadge item={item} /> },
  { id: 'effect', header: '恢复结果', width: listWidths[3], cell: (item) => <EffectBadge item={item} /> },
  { id: 'times', header: '开始/结束时间', width: listWidths[4], cell: (item) => <TimeCell item={item} /> },
  {
    id: 'overview',
    header: '任务概述',
    cell: (item) => (
      <div className="flex min-w-0 items-center gap-2">
        <Hint content={item.overview}>
          <div className="line-clamp-2 flex-1 break-words text-foreground">{item.overview || '—'}</div>
        </Hint>
        <ChevronRight size={14} className="ml-auto shrink-0 text-muted-foreground" aria-hidden />
      </div>
    ),
  },
];

function CompactList({ items, onOpen }: { items: RecoveryListItem[]; onOpen(id: string): void }) {
  return (
    <ul className="m-0 list-none divide-y divide-border p-0">
      {items.map((item) => (
        <li key={item.taskId} className="p-4">
          <div className="grid grid-cols-2 gap-3">
            <div className="min-w-0">
              <div className="text-xs text-muted-foreground">Bot ID</div>
              <BotCell item={item} />
            </div>
            <div className="min-w-0">
              <div className="text-xs text-muted-foreground">会话标识</div>
              <SessionCell item={item} />
            </div>
            <div>
              <div className="mb-1 text-xs text-muted-foreground">任务状态</div>
              <StatusBadge item={item} />
            </div>
            <div>
              <div className="mb-1 text-xs text-muted-foreground">恢复结果</div>
              <EffectBadge item={item} />
            </div>
          </div>
          <div className="my-3">
            <TimeCell item={item} />
          </div>
          <div className="text-xs text-muted-foreground">任务概述</div>
          <p className="my-1 break-words text-sm">{item.overview || '—'}</p>
          <Button
            size="sm"
            variant="ghost"
            type="button"
            className="px-0 text-primary"
            onClick={() => onOpen(item.taskId)}
          >
            查看详情 <ChevronRight size={14} aria-hidden />
          </Button>
        </li>
      ))}
    </ul>
  );
}

type ListFilters = {
  botId?: string;
  env?: string;
  botName?: string;
  status?: RecoveryStatus;
  page: number;
  pageSize: number;
};
export function RecoveryList({
  spaceId,
  params,
  setParams,
  onOpen,
}: {
  spaceId: string;
  params: URLSearchParams;
  setParams(values: Record<string, string | undefined>): void;
  onOpen(taskId: string): void;
}) {
  const [settings, setSettings] = useState(false);
  const size = Number(params.get('recoveryPageSize'));
  const requestedPage = Number(params.get('recoveryPage'));
  const filters: ListFilters = {
    botId: params.get('recoveryBot') || undefined,
    env: params.get('recoveryEnv') || undefined,
    botName: params.get('recoveryBotName') || undefined,
    page: Number.isSafeInteger(requestedPage) && requestedPage > 0 ? requestedPage : 1,
    pageSize: pageSizes.includes(size) ? size : 20,
  };
  const status = params.get('recoveryStatus');
  if (statuses.some(({ value }) => value === status)) filters.status = status as RecoveryStatus;
  const { botId, env, page, pageSize } = filters;
  const taskStatus = filters.status;
  const load = useCallback(
    () => recoveryService.list({ botId, env, status: taskStatus, page, pageSize }),
    [botId, env, taskStatus, page, pageSize],
  );
  const result = useResource(load);
  const items = result.data?.items ?? [];

  return (
    <section aria-label="自愈记录" className="min-w-0">
      <header className="mb-4 flex flex-wrap items-center gap-2">
        <h2 className="m-0 text-base font-semibold">自愈记录</h2>
        <span className="text-xs text-muted-foreground">最新任务优先</span>
        <Button type="button" size="sm" className="ml-auto" onClick={() => setSettings(true)}>
          自愈设置
        </Button>
      </header>
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <RecoveryBotFilter
          spaceId={spaceId}
          label={filters.botName || botId}
          onSelect={(bot) =>
            setParams({
              recoveryBot: bot?.botId,
              recoveryEnv: bot?.env,
              recoveryBotName: bot?.botName,
              recoveryPage: undefined,
            })
          }
        />
        <FilterSelect
          label="任务状态"
          value={taskStatus || 'all'}
          onChange={(value) =>
            setParams({ recoveryStatus: value === 'all' ? undefined : value, recoveryPage: undefined })
          }
          options={[{ value: 'all', label: '全部状态' }, ...statuses]}
        />
        <Button
          type="button"
          size="sm"
          variant="outline"
          className="ml-auto"
          leftIcon={<RefreshCw size={14} />}
          disabled={result.loading}
          onClick={result.reload}
        >
          刷新
        </Button>
      </div>
      <ErrorBox message={result.error} retry={result.reload} />
      {result.loading ? (
        <TablePanel>
          <ListSkeleton widths={listWidths} ariaLabel="自愈任务加载中" />
        </TablePanel>
      ) : (
        result.data &&
        (items.length ? (
          <div className="tc-recovery-list-layout">
            <TablePanel>
              <div className="tc-recovery-list-table">
                <DataTable
                  columns={columns}
                  rows={items}
                  getRowKey={(item) => item.taskId}
                  onRowClick={(item) => onOpen(item.taskId)}
                  rowHeight="h-18"
                  ariaLabel="自愈任务列表"
                />
              </div>
              <div className="tc-recovery-list-cards">
                <CompactList items={items} onOpen={onOpen} />
              </div>
            </TablePanel>
          </div>
        ) : (
          <TablePanel>
            <Empty
              title={result.data.total ? '当前页没有任务' : botId || taskStatus ? '没有符合条件的任务' : '暂无自愈任务'}
            />
          </TablePanel>
        ))
      )}
      {result.data && (
        <Pagination
          current={page}
          pageSize={pageSize}
          total={result.data.total}
          layout="between"
          showQuickJumper
          showPageInfo
          className="mt-4"
          pageSizeOptions={pageSizes}
          onChange={(next) => setParams({ recoveryPage: String(next) })}
          onPageSizeChange={(next) => setParams({ recoveryPageSize: String(next), recoveryPage: undefined })}
        />
      )}
      {settings && <RecoverySettings spaceId={spaceId} onClose={() => setSettings(false)} onChanged={result.reload} />}
    </section>
  );
}
