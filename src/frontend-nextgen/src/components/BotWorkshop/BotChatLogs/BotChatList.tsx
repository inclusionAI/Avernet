import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Empty } from '@/components/ui/Empty';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/Select';
import type { BotChatPage } from '@/domain/botChats';
import { ChevronLeft, ChevronRight, Eye, Target } from 'lucide-react';

interface Props {
  page?: BotChatPage;
  onOpen: (traceId: string) => void;
  onPage: (page: number) => void;
  onPageSize: (limit: number) => void;
}

const text = (value: unknown) => {
  if (value === undefined || value === null || value === '') return '-';
  return typeof value === 'string' ? value : JSON.stringify(value);
};
const short = (value: unknown, length = 36) => {
  const rendered = text(value);
  return rendered.length > length ? `${rendered.slice(0, length)}…` : rendered;
};
const cost = (value: number) => (value > 0 ? `$${value.toFixed(6)}` : '-');
const statusTone = (status: string) => (status === 'SUCCESS' ? 'success' : status === 'RUNNING' ? 'primary' : 'error');

export function BotChatList({ page, onOpen, onPage, onPageSize }: Props) {
  if (!page?.items.length) return <Empty compact title="暂无日志" description="调整筛选条件后重试。" />;
  const pageCount = Math.max(1, Math.ceil(page.total / page.limit));
  return (
    <>
      <div className="app-scrollbar overflow-x-auto rounded-xl border border-[var(--color-border)] bg-[var(--color-card)]">
        <table className="w-full min-w-[1540px] border-collapse text-left text-sm">
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
                <td className="whitespace-nowrap px-4 py-4 font-mono text-xs" title={item.id}>
                  {item.id}
                </td>
                <td className="px-4 py-4">{item.bizScene ?? '-'}</td>
                <td className="whitespace-nowrap px-4 py-4 font-mono text-xs">{item.bizTaskId ?? '-'}</td>
                <td className="whitespace-nowrap px-4 py-4 font-mono text-xs">{item.groupId ?? '-'}</td>
                <td className="whitespace-nowrap px-4 py-4 font-mono text-xs">{item.sessionId ?? '-'}</td>
                <td className="max-w-64 break-all px-4 py-4 font-mono text-xs" title={item.sessionKey}>
                  {item.sessionKey ?? '-'}
                </td>
                <td className="max-w-64 px-4 py-4" title={text(item.input)}>
                  <span className="flex items-start gap-1">
                    <Target className="mt-0.5 size-3.5 shrink-0 text-[var(--color-error)]" />
                    {short(item.input)}
                  </span>
                </td>
                <td className="max-w-56 px-4 py-4" title={item.outputPreview}>
                  {short(item.outputPreview)}
                </td>
                <td className="whitespace-nowrap px-4 py-4">{item.model ?? '-'}</td>
                <td className="whitespace-nowrap px-4 py-4 font-mono text-xs">
                  {cost(item.totalCost)}
                  <div className="mt-1 text-[var(--color-muted)]">{item.totalTokens} tokens</div>
                </td>
                <td className="whitespace-nowrap px-4 py-4">
                  <Badge tone={statusTone(item.status)}>{item.status}</Badge>
                </td>
                <td className="whitespace-nowrap px-4 py-4">
                  {item.timestamp ? new Date(item.timestamp).toLocaleString() : '-'}
                </td>
                <td className="sticky right-0 border-l border-[var(--color-border)] bg-[var(--color-card)] px-4 py-4 group-hover:bg-[var(--color-panel-muted)]">
                  <Button
                    variant="ghost"
                    size="sm"
                    className="text-[var(--color-primary)]"
                    leftIcon={<Eye className="size-4" />}
                    onClick={() => onOpen(item.id)}
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
