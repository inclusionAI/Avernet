import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { DataTable, type DataTableColumn } from '@/components/ui/DataTable';
import { Empty } from '@/components/ui/Empty';
import { Pagination } from '@/components/ui/Pagination';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';
import type { BotChatDetailSelection, BotChatPage } from '@/domain/botChats';
import { Eye, Target } from 'lucide-react';

interface Props {
  page?: BotChatPage;
  onOpen: (selection: BotChatDetailSelection) => void;
  onPage: (page: number) => void;
  onPageSize: (limit: number) => void;
}

const text = (value: unknown) => {
  if (value === undefined || value === null || value === '') return '-';
  return typeof value === 'string' ? value : JSON.stringify(value);
};
const preview = (value: unknown, length = 96) => {
  const rendered = text(value);
  return rendered.length > length ? `${rendered.slice(0, length)}…` : rendered;
};
const cost = (value: number) => (value > 0 ? `$${value.toFixed(6)}` : '-');
const statusTone = (status: string) => (status === 'SUCCESS' ? 'success' : status === 'RUNNING' ? 'primary' : 'error');

function Identifier({ value, muted }: { value?: string; muted?: boolean }) {
  const rendered = value || '-';

  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          <span
            className={`block max-w-full truncate font-mono text-xs ${
              muted ? 'text-[11px] text-muted-foreground' : 'text-foreground'
            }`}
          >
            {rendered}
          </span>
        </TooltipTrigger>
        <TooltipContent side="top" className="max-w-sm break-all font-mono text-[11px]">
          {rendered}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

function Preview({ value, accent }: { value: unknown; accent?: boolean }) {
  const rendered = text(value);

  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          <span
            className={`block max-w-full overflow-hidden text-ellipsis [display:-webkit-box] [-webkit-box-orient:vertical] [-webkit-line-clamp:2] break-words leading-5 ${
              accent ? 'text-foreground' : 'text-muted-foreground'
            }`}
          >
            {preview(value)}
          </span>
        </TooltipTrigger>
        <TooltipContent side="top" className="max-w-lg whitespace-pre-wrap break-words text-left leading-5">
          {rendered}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

export function BotChatList({ page, onOpen, onPage, onPageSize }: Props) {
  if (!page?.items.length) return <Empty compact title="暂无日志" description="调整筛选条件后重试。" />;

  // 列结构精编（design-extension §3 修订）：二级标识不再平铺成五面 ID 墙，
  // 而是降为簇内堆叠元数据——traceID 一枝独醒作行首锚点，输入/输出载荷成为视觉前景。
  const columns: DataTableColumn<BotChatPage['items'][number]>[] = [
    {
      id: 'traceId',
      header: 'traceID',
      width: 'w-[160px]',
      cell: (item) => <Identifier value={item.id} />,
    },
    {
      id: 'biz',
      header: '业务场景 / 任务',
      width: 'w-[180px]',
      cell: (item) => (
        <div className="min-w-0 space-y-0.5">
          <div className="block truncate text-xs">{item.bizScene ?? '-'}</div>
          {item.bizTaskId ? <Identifier value={item.bizTaskId} muted /> : null}
          {item.groupId ? <Identifier value={`群 ${item.groupId}`} muted /> : null}
        </div>
      ),
    },
    {
      id: 'session',
      header: 'sessionID / Key',
      width: 'w-[190px]',
      cell: (item) => (
        <div className="min-w-0 space-y-0.5">
          <Identifier value={item.sessionId} />
          {item.sessionKey ? <Identifier value={item.sessionKey} muted /> : null}
        </div>
      ),
    },
    {
      id: 'input',
      header: '输入',
      width: 'w-[220px]',
      cell: (item) => (
        <span className="flex min-w-0 items-start gap-1">
          <Target className="mt-0.5 size-3.5 shrink-0 text-destructive" />
          <Preview value={item.input} accent />
        </span>
      ),
    },
    {
      id: 'output',
      header: '输出',
      width: 'w-[220px]',
      cell: (item) => <Preview value={item.outputPreview} />,
    },
    {
      id: 'model',
      header: '模型',
      width: 'w-[120px]',
      cell: (item) => <span className="block truncate">{item.model ?? '-'}</span>,
    },
    {
      id: 'cost',
      header: '消耗 / Token',
      width: 'w-[110px]',
      align: 'end',
      cell: (item) => (
        <span className="block font-mono text-xs tabular-nums">
          {cost(item.totalCost)}
          <span className="mt-1 block text-[11px] text-muted-foreground">{item.totalTokens} tokens</span>
        </span>
      ),
    },
    {
      id: 'status',
      header: '状态',
      width: 'w-[84px]',
      cell: (item) => <Badge tone={statusTone(item.status)}>{item.status}</Badge>,
    },
    {
      id: 'timestamp',
      header: '时间',
      width: 'w-[150px]',
      cell: (item) => (
        <span className="whitespace-nowrap text-xs text-muted-foreground">
          {item.timestamp ? new Date(item.timestamp).toLocaleString() : '-'}
        </span>
      ),
    },
    {
      id: 'actions',
      header: '操作',
      width: 'w-[120px]',
      // sticky 右置操作列（首页 useBotTableColumns 同款语法，表头底色与其对齐）。
      className: 'sticky right-0 z-10 bg-card group-hover:bg-muted/40',
      headerClassName: 'sticky right-0 z-10 bg-muted/50 border-l border-border/60',
      cell: (item) => (
        <Button
          variant="ghost"
          size="sm"
          className="text-primary"
          leftIcon={<Eye className="size-4" />}
          onClick={() => onOpen({ traceId: item.id, sessionId: item.sessionId, botId: item.botId })}
        >
          详情
        </Button>
      ),
    },
  ];

  return (
    <>
      {/* 结果面板：与首页嵌套语法一致（外卡内 round-[10px] 有边结果面板）。 */}
      <div className="overflow-hidden rounded-[10px] border border-border/60 bg-card">
        <DataTable
          ariaLabel="Bot 对话日志列表"
          columns={columns}
          rows={page.items}
          getRowKey={(item) => item.id}
          rowHeight="h-18"
          minWidth="min-w-[1554px]"
        />
      </div>
      <Pagination
        current={page.page}
        pageSize={page.limit}
        total={page.total}
        pageSizeOptions={[20, 50, 100]}
        onChange={onPage}
        onPageSizeChange={onPageSize}
        layout="between"
        className="mt-4"
      />
    </>
  );
}