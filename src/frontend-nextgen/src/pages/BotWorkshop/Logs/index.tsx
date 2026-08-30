import { BotChatDetail } from '@/components/BotWorkshop/BotChatLogs/BotChatDetail';
import { BotChatFilters } from '@/components/BotWorkshop/BotChatLogs/BotChatFilters';
import { BotChatList } from '@/components/BotWorkshop/BotChatLogs/BotChatList';
import { PageHeader } from '@/components/Common/PageHeader';
import { Button } from '@/components/ui/Button';
import { Empty } from '@/components/ui/Empty';
import { useBotChats } from '@/hooks/useBotChats';
import { ArrowLeft, LoaderCircle } from 'lucide-react';
import React from 'react';

const BotChatLogsPage: React.FC = () => {
  const logs = useBotChats();
  const header = (
    <PageHeader
      eyebrow="Bot 工坊"
      title={`日志 · ${logs.context?.botName ?? 'Bot'}`}
      description={
        logs.context
          ? `Bot ID：${logs.context.botId}。查询对话 Trace，并查看完整输入、输出和 Observation 树。`
          : '查询 Bot 对话 Trace。'
      }
      actions={
        <Button variant="secondary" leftIcon={<ArrowLeft className="size-4" />} onClick={logs.backToWorkshop}>
          返回 Bot 工坊
        </Button>
      }
    />
  );

  return (
    <main className="app-scrollbar h-full overflow-y-auto">
      <div className="w-full space-y-6 p-4 sm:p-6 2xl:p-8">
        {header}
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
            botName={logs.context?.botName ?? 'Bot'}
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
                onOpen={(traceId) => void logs.openDetail(traceId)}
                onPage={(page) => void logs.changePage(page)}
                onPageSize={(limit) => void logs.changePageSize(limit)}
              />
            )}
          </div>
        )}
      </div>
    </main>
  );
};

export default BotChatLogsPage;
