import BotAvatar from '@/components/BotWorkshop/BotAvatar';
import { BotChatDetail } from '@/components/BotWorkshop/BotChatLogs/BotChatDetail';
import { BotChatFilters } from '@/components/BotWorkshop/BotChatLogs/BotChatFilters';
import { BotChatList } from '@/components/BotWorkshop/BotChatLogs/BotChatList';
import { Button } from '@/components/ui/Button';
import { Empty } from '@/components/ui/Empty';
import { useBotChats } from '@/hooks/useBotChats';
import { ArrowLeft, LoaderCircle } from 'lucide-react';
import React from 'react';

const BotChatLogsPage: React.FC = () => {
  const logs = useBotChats();
  const botName = logs.context?.botName ?? 'Bot';

  return (
    <main className="flex h-full min-h-0 flex-col bg-background">
      <header className="flex h-16 shrink-0 items-center gap-3 border-b border-border bg-card px-4 sm:px-6">
        <Button
          variant="ghost"
          size="icon"
          aria-label="返回 Bot 工坊"
          onClick={logs.backToWorkshop}
          leftIcon={<ArrowLeft className="size-4" />}
        />
        <BotAvatar name={botName} />
        <div className="min-w-0 flex-1">
          <h1 className="m-0 truncate text-base font-semibold">日志 · {botName}</h1>
          <p className="m-0 mt-0.5 truncate text-xs text-muted-foreground">
            {logs.context ? `Bot ID：${logs.context.botId} · 查询对话 Trace` : '查询 Bot 对话 Trace'}
          </p>
        </div>
      </header>
      <div className="app-scrollbar min-h-0 flex-1 overflow-y-auto">
        <div className="w-full space-y-6 p-4 sm:p-6 2xl:p-8">
          {logs.initializationError ? (
            <Empty
              title="无法打开日志"
              description={logs.initializationError}
              action={<Button onClick={logs.backToWorkshop}>返回 Bot 工坊</Button>}
            />
          ) : logs.detail ? (
            <BotChatDetail
              detail={logs.detail}
              related={logs.related}
              relationScope={logs.relationScope}
              relatedLoading={logs.relatedLoading}
              error={logs.error}
              botName={botName}
              botId={logs.context?.botId ?? ''}
              onBack={logs.backToList}
              onRelation={(scope) => void logs.loadRelated(scope)}
              onOpenTrace={(traceId) => void logs.openDetail(traceId)}
              onLoadMore={() => void logs.loadMoreRelated()}
            />
          ) : (
            <div className="space-y-4">
              <BotChatFilters
                value={logs.filters}
                loading={logs.loading}
                onChange={logs.setFilter}
                onQuery={() => void logs.query()}
                onReset={() => void logs.resetFilters()}
              />
              {logs.loading && !logs.page ? (
                <div className="flex min-h-64 items-center justify-center gap-2 text-sm text-[var(--color-muted)]">
                  <LoaderCircle className="size-4 animate-spin" />
                  正在加载日志
                </div>
              ) : logs.error ? (
                <Empty
                  compact
                  title="日志加载失败"
                  description={logs.error}
                  action={
                    <Button variant="secondary" onClick={() => void logs.query()}>
                      重试
                    </Button>
                  }
                />
              ) : logs.detailLoading ? (
                <div className="flex min-h-64 items-center justify-center gap-2 text-sm text-[var(--color-muted)]">
                  <LoaderCircle className="size-4 animate-spin" />
                  正在加载详情
                </div>
              ) : (
                <BotChatList
                  page={logs.page}
                  onOpen={(selection) => void logs.openDetail(selection)}
                  onPage={(page) => void logs.changePage(page)}
                  onPageSize={(limit) => void logs.changePageSize(limit)}
                />
              )}
            </div>
          )}
        </div>
      </div>
    </main>
  );
};

export default BotChatLogsPage;
