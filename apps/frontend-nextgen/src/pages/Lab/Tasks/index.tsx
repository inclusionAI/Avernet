import { PageHeader } from '@/components/Common/PageHeader';
import { Button } from '@/components/ui/Button';
import { useHumanIdentity } from '@/hooks/useHumanIdentity';
import { UserTaskTab } from '@/pages/MyTask/components/UserTaskTab';
import { useMyTaskTasks } from '@/pages/MyTask/hooks/useMyTaskTasks';
import type { UserTaskStatusFilter } from '@/pages/MyTask/userTaskUtils';
import { useOwnedBots } from '@/pages/Workspace/hooks/useOwnedBots';
import { history } from '@umijs/max';
import { useMemo, useState } from 'react';
import { TaskArtifactsDrawer } from './TaskArtifactsDrawer';

const DEFAULT_PAGE = 1;
const DEFAULT_PAGE_SIZE = 10;

function normalizeBotNameKey(value?: string | null): string {
  return value?.trim().toLowerCase() ?? '';
}

/**
 * 实验室「任务」体验页(PRD:TeamClaw-demo /tasks):
 * 查看跨Bot、跨群的协同任务,可在对话或协作群中发起此类任务。
 * 与「我的任务」不同,本页不做身份切换(定时任务是 Bot 管理侧能力,与本页无关),
 * 恒以登录人工号拉取用户协同任务;表格/类别筛选复用 MyTask 现有组件，
 * 仅在实验室展示模式下调整列布局，并通过 TaskPanel 产物组件展示任务产物。
 */
export default function LabTasksPage() {
  const { identity: humanIdentity } = useHumanIdentity();
  const ownerUserId = humanIdentity?.userId.trim() ?? '';

  const [page, setPage] = useState(DEFAULT_PAGE);
  const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE);
  const [statusFilter, setStatusFilter] = useState<UserTaskStatusFilter>('all');
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);

  const { taskRecords, total, loading, error, refresh } = useMyTaskTasks(
    ownerUserId,
    page,
    pageSize,
    statusFilter,
    Boolean(ownerUserId),
  );
  const { chatBots, isLoading: ownedBotsLoading } = useOwnedBots(ownerUserId || null, Boolean(ownerUserId));
  const botNameMap = useMemo(() => {
    const map: Record<string, string> = {};
    chatBots.forEach((bot) => {
      const botName = bot.displayName || bot.botId;
      const botIdKey = normalizeBotNameKey(bot.botId);
      const realBotIdKey = normalizeBotNameKey(bot.realBotId);
      if (botIdKey) map[botIdKey] = botName;
      if (realBotIdKey) map[realBotIdKey] = botName;
    });
    return map;
  }, [chatBots]);

  return (
    <main className="app-scrollbar h-full min-h-0 overflow-y-auto">
      <div className="mx-auto flex w-full max-w-[1600px] flex-col gap-6 px-4 py-4 sm:px-6 sm:py-6 2xl:px-8">
        <PageHeader
          title="任务"
          description={
            <span>
              查看跨Bot、跨群的协同任务，可在
              <Button
                variant="link"
                className="h-auto px-1 py-0 text-[13px] leading-5"
                onClick={() => history.push('/workspace/chat')}
              >
                对话
              </Button>
              或
              <Button
                variant="link"
                className="h-auto px-1 py-0 text-[13px] leading-5"
                onClick={() => history.push('/workspace/collaboration')}
              >
                协作群
              </Button>
              中发起此类任务
            </span>
          }
        />
        <UserTaskTab
          taskRecords={taskRecords}
          total={total}
          page={page}
          pageSize={pageSize}
          loading={loading || ownedBotsLoading}
          error={error}
          statusFilter={statusFilter}
          onStatusFilterChange={(status) => {
            setStatusFilter(status);
            setPage(DEFAULT_PAGE);
          }}
          onRetry={() => void refresh()}
          onSelectTask={setSelectedTaskId}
          selectedTaskId={selectedTaskId}
          onPageChange={setPage}
          onPageSizeChange={(next) => {
            setPageSize(next);
            setPage(DEFAULT_PAGE);
          }}
          botNameMap={botNameMap}
          displayVariant="lab"
        />
      </div>
      <TaskArtifactsDrawer taskId={selectedTaskId} ownerUserId={ownerUserId} onClose={() => setSelectedTaskId(null)} />
    </main>
  );
}
