import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Empty } from '@/components/ui/Empty';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/Select';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';
import type { BotChatDetailSelection, BotChatPage } from '@/domain/botChats';
import { ChevronLeft, ChevronRight, Eye, Target } from 'lucide-react';

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

function Identifier({ value }: { value?: string }) {
  const rendered = value || '-';

  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          <span className="block max-w-full truncate font-mono text-xs text-[var(--color-fg)]">{rendered}</span>
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
              accent ? 'text-[var(--color-fg)]' : 'text-[var(--color-muted)]'
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
  const pageCount = Math.max(1, Math.ceil(page.total / page.limit));
  return (
    <>
      <div className="app-scrollbar overflow-x-auto rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] shadow-sm">
        <table className="w-full min-w-[1280px] table-fixed border-collapse text-left text-sm">
          <colgroup>
            <col className="w-[170px]" />
            <col className="w-[104px]" />
            <col className="w-[110px]" />
            <col className="w-[170px]" />
            <col className="w-[170px]" />
            <col className="w-[230px]" />
            <col className="w-[230px]" />
            <col className="w-[230px]" />
            <col className="w-[120px]" />
            <col className="w-[110px]" />
            <col className="w-[86px]" />
            <col className="w-[150px]" />
            <col className="w-[90px]" />
          </colgroup>
          <thead className="bg-[var(--color-panel-muted)] text-[var(--color-muted)]">
            <tr>
              {[
                'traceID',
                '业务场景',
                '业务任务',
                '群ID',
                'sessionID',
                'sessionKey',
                '输入(INPUT)',
                '输出(OUTPUT)',
                '模型',
                '消耗/成本',
                '状态',
                '时间',
              ].map((label) => (
                <th
                  key={label}
                  className="whitespace-nowrap border-b border-[var(--color-border)] px-4 py-3 font-medium"
                >
                  {label}
                </th>
              ))}
              <th className="sticky right-0 z-10 whitespace-nowrap border-b border-l border-[var(--color-border)] bg-[var(--color-panel-muted)] px-4 py-3 font-medium">
                操作
              </th>
            </tr>
          </thead>
          <tbody>
            {page.items.map((item) => (
              <tr
                key={item.id}
                className="group border-b border-[var(--color-border)] last:border-0 hover:bg-[var(--color-panel-muted)]"
              >
                <td className="px-4 py-3 align-top">
                  <Identifier value={item.id} />
                </td>
                <td className="truncate px-4 py-3 align-top">{item.bizScene ?? '-'}</td>
                <td className="px-4 py-3 align-top">
                  <Identifier value={item.bizTaskId} />
                </td>
                <td className="px-4 py-3 align-top">
                  <Identifier value={item.groupId} />
                </td>
                <td className="px-4 py-3 align-top">
                  <Identifier value={item.sessionId} />
                </td>
                <td className="px-4 py-3 align-top">
                  <Identifier value={item.sessionKey} />
                </td>
                <td className="px-4 py-3 align-top">
                  <span className="flex min-w-0 items-start gap-1">
                    <Target className="mt-0.5 size-3.5 shrink-0 text-[var(--color-error)]" />
                    <Preview value={item.input} accent />
                  </span>
                </td>
                <td className="px-4 py-3 align-top">
                  <Preview value={item.outputPreview} />
                </td>
                <td className="truncate px-4 py-3 align-top">{item.model ?? '-'}</td>
                <td className="px-4 py-3 align-top font-mono text-xs">
                  {cost(item.totalCost)}
                  <div className="mt-1 text-[var(--color-muted)]">{item.totalTokens} tokens</div>
                </td>
                <td className="px-4 py-3 align-top">
                  <Badge tone={statusTone(item.status)}>{item.status}</Badge>
                </td>
                <td className="whitespace-nowrap px-4 py-3 align-top text-xs text-[var(--color-muted)]">
                  {item.timestamp ? new Date(item.timestamp).toLocaleString() : '-'}
                </td>
                <td className="sticky right-0 border-l border-[var(--color-border)] bg-[var(--color-card)] px-3 py-3 align-top group-hover:bg-[var(--color-panel-muted)]">
                  <Button
                    variant="ghost"
                    size="sm"
                    className="text-[var(--color-primary)]"
                    leftIcon={<Eye className="size-4" />}
                    onClick={() => onOpen({ traceId: item.id, sessionId: item.sessionId, botId: item.botId })}
                  >
                    详情
                  </Button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="mt-3 flex flex-wrap items-center justify-between gap-3 text-xs text-[var(--color-muted)]">
        <span>共 {page.total} 条</span>
        <div className="flex items-center gap-2">
          <Button
            variant="secondary"
            size="icon"
            aria-label="上一页"
            disabled={page.page <= 1}
            onClick={() => onPage(page.page - 1)}
          >
            <ChevronLeft className="size-4" />
          </Button>
          <span>
            {page.page} / {pageCount}
          </span>
          <Button
            variant="secondary"
            size="icon"
            aria-label="下一页"
            disabled={!page.hasMore}
            onClick={() => onPage(page.page + 1)}
          >
            <ChevronRight className="size-4" />
          </Button>
        </div>
        <div className="flex items-center gap-2">
          <span id="bot-chat-page-size">每页</span>
          <Select value={String(page.limit)} onValueChange={(value) => onPageSize(Number(value))}>
            <SelectTrigger className="h-8 w-20" aria-labelledby="bot-chat-page-size">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {[20, 50, 100].map((size) => (
                <SelectItem key={size} value={String(size)}>
                  {size}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <span>条</span>
        </div>
      </div>
    </>
  );
}
