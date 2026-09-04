import TaskCard from '@/components/CollaborationSquare/TaskCard';
import { TaskDetailModal } from '@/components/CollaborationSquare/TaskDetailModal';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { Empty } from '@/components/ui/Empty';
import { IconButton } from '@/components/ui/IconButton';
import { Input } from '@/components/ui/Input';
import { Segmented } from '@/components/ui/Segmented';
import { Skeleton } from '@/components/ui/Skeleton';
import { TASK_STATUS_CONFIG, type PublicTask, type TaskStatusFilter } from '@/domain/collaborationSquare/types';
import { RefreshCw, Search, X } from 'lucide-react';

const STATUS_OPTIONS: { value: TaskStatusFilter; label: string }[] = [
  { value: 'all', label: '全部' },
  { value: 'pending_claim', label: '待认领' },
  { value: 'claimed', label: '已认领' },
  { value: 'reviewing', label: '待验收' },
  { value: 'completed', label: '已完成' },
];

/** 任务广场面板展示层 view model：由 Shell 从 `useCollaborationSquare('task')` 组装后注入，面板纯展示。 */
export interface TaskCatalogViewModel {
  tasks: PublicTask[];
  taskQuery: string;
  taskStatusFilter: TaskStatusFilter;
  setTaskQuery: (query: string) => void;
  setTaskStatusFilter: (filter: TaskStatusFilter) => void;
  resetTaskFilters: () => void;
  loading: boolean;
  error: string | null;
  /** 是否还有更多页（驱动无限滚动 / 「加载更多」提示）；分页与过滤均在服务端，total 来自接口。 */
  hasMore: boolean;
  /** 下一页加载中（滚动触发的预取由 Shell 统一编排，面板只负责展示态）。 */
  loadingMore: boolean;
  loadMore: () => void;
  loadMoreError: string | null;
  reload: () => void;
  openTaskDetail: (task: PublicTask) => void;
  selectedTaskId: string | null;
  taskDetail: PublicTask | null;
  detailLoading: boolean;
  closeTaskDetail: () => void;
}

function TaskLoadingState() {
  return (
    <div aria-label="正在加载任务广场" className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
      {[0, 1, 2].map((item) => (
        <Card key={item}>
          <Skeleton.Card />
        </Card>
      ))}
    </div>
  );
}

/** 筛选无结果时的描述：带当前状态文案与关键词（文字通道，不依赖颜色）。 */
function describeEmptyFilter(query: string, status: TaskStatusFilter): string {
  const parts: string[] = [];
  if (status !== 'all') parts.push(`状态：${TASK_STATUS_CONFIG[status].label}`);
  if (query.trim()) parts.push(`关键词：${query.trim()}`);
  const tail = parts.length > 0 ? `（${parts.join('，')}）` : '';
  return `当前筛选下暂无任务${tail}，尝试更换关键词或清除筛选。`;
}

/**
 * 公开任务广场只读目录面板：关键词搜索 + 状态分段筛选 + 结果摘要 + 任务卡网格 + loading/empty/error 态，
 * 并挂载只读详情弹层（`TaskDetailModal`）。消费 Shell 组装的 {@link TaskCatalogViewModel}；面板纯展示，
 * 不直接 import Service/Store、不弹 toast。弹层 open 受 `selectedTaskId` 驱动，关闭经 `closeTaskDetail` 清理。
 */
export function PublicTaskCatalogPanel({ vm }: { vm: TaskCatalogViewModel }) {
  const { tasks, taskQuery, taskStatusFilter } = vm;
  const hasFilters = taskQuery.trim() !== '' || taskStatusFilter !== 'all';
  const showGrid = !vm.loading && !vm.error && tasks.length > 0;
  const showEmpty = !vm.loading && !vm.error && tasks.length === 0;

  return (
    <>
      <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <div className="relative min-w-0 flex-1 md:max-w-md">
          <Search aria-hidden className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            aria-label="搜索任务"
            value={taskQuery}
            placeholder="搜索任务..."
            onChange={(event) => vm.setTaskQuery(event.target.value)}
            className="pl-9 pr-10"
          />
          {taskQuery && (
            <IconButton
              label="清除搜索"
              icon={<X aria-hidden className="h-4 w-4" />}
              onClick={() => vm.setTaskQuery('')}
              className="absolute right-0 top-0"
            />
          )}
        </div>
        <div role="group" aria-label="任务状态筛选">
          <Segmented value={taskStatusFilter} options={STATUS_OPTIONS} onChange={vm.setTaskStatusFilter} />
        </div>
      </div>

      {vm.loading && <TaskLoadingState />}

      {!vm.loading && vm.error && (
        <Card>
          <Empty
            title="任务广场加载失败"
            description={vm.error}
            action={
              <Button onClick={vm.reload} leftIcon={<RefreshCw aria-hidden className="h-4 w-4" />}>
                重新加载
              </Button>
            }
          />
        </Card>
      )}

      {showGrid && hasFilters && (
        <div className="flex items-center justify-between gap-2 text-xs text-muted-foreground">
          <span>命中 {tasks.length} 个任务</span>
          <Button variant="link" size="sm" onClick={vm.resetTaskFilters}>
            清除筛选
          </Button>
        </div>
      )}

      {showGrid && (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
          {tasks.map((task) => (
            <TaskCard key={task.id} task={task} onOpenDetail={vm.openTaskDetail} />
          ))}
        </div>
      )}

      {showGrid && vm.loadingMore && (
        <div aria-live="polite" className="text-center text-xs text-muted-foreground">
          正在加载更多...
        </div>
      )}

      {!vm.loading && !vm.error && vm.loadMoreError && (
        <Card className="flex items-center justify-between gap-3 p-4">
          <p className="m-0 text-sm text-muted-foreground">{vm.loadMoreError}</p>
          <Button variant="secondary" size="sm" onClick={vm.loadMore}>
            重试
          </Button>
        </Card>
      )}

      {showEmpty && !hasFilters && (
        <Card>
          <Empty title="当前暂无公开任务" description="公开 BBS 求助任务发布后将出现在这里。" />
        </Card>
      )}

      {showEmpty && hasFilters && (
        <Card>
          <Empty
            title="未找到符合条件的任务"
            description={describeEmptyFilter(taskQuery, taskStatusFilter)}
            action={
              <Button variant="outline" size="sm" onClick={vm.resetTaskFilters}>
                清除筛选
              </Button>
            }
          />
        </Card>
      )}

      <TaskDetailModal
        open={Boolean(vm.selectedTaskId)}
        task={vm.taskDetail}
        loading={vm.detailLoading}
        onClose={vm.closeTaskDetail}
      />
    </>
  );
}
