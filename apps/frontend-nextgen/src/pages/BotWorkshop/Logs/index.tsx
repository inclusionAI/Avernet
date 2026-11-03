import { BotSubpageShell } from '@/components/BotWorkshop/BotSubpageShell';
import { BotChatDetail } from '@/components/BotWorkshop/BotChatLogs/BotChatDetail';
import { BotChatFilters } from '@/components/BotWorkshop/BotChatLogs/BotChatFilters';
import { BotChatList } from '@/components/BotWorkshop/BotChatLogs/BotChatList';
import { Button } from '@/components/ui/Button';
import { Empty } from '@/components/ui/Empty';
import { useBotChats } from '@/hooks/useBotChats';
import { LoaderCircle } from 'lucide-react';
import React from 'react';

const BotChatLogsPage: React.FC = () => {
  const logs = useBotChats();
  const botName = logs.context?.botName ?? 'Bot';

  return (
    <BotSubpageShell
      onBack={logs.backToWorkshop}
      botName={botName}
      title={`日志 · ${botName}`}
      subtitle={logs.context ? `Bot ID：${logs.context.botId} · 查询对话 Trace` : '查询 Bot 对话 Trace'}
    >
      {logs.initializationError ? (
        <section className="rounded-xl border border-border/60 bg-card">
          <Empty
            title="无法打开日志"
            description={logs.initializationError}
            action={<Button onClick={logs.backToWorkshop}>返回 Bot 管理</Button>}
          />
        </section>
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
        <section className="rounded-xl border border-border/60 bg-card p-4 sm:p-6">
          <div className="space-y-4">
            <BotChatFilters
              value={logs.filters}
              loading={logs.loading}
              onChange={logs.setFilter}
              onQuery={() => void logs.query()}
              onReset={() => void logs.resetFilters()}
            />
            {logs.loading && !logs.page ? (
              <div className="flex min-h-64 items-center justify-center gap-2 text-sm text-muted-foreground">
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
              <div className="flex min-h-64 items-center justify-center gap-2 text-sm text-muted-foreground">
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
        </section>
      )}
    </BotSubpageShell>
  );
};

export default BotChatLogsPage;