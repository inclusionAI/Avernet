import { Badge } from '@/components/ui/Badge';
import { Card, CardContent, CardFooter, CardHeader, CardTitle } from '@/components/ui/Card';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';
import {
  getPublicTaskStatusPresentation,
  type PlazaTaskStatus,
  type PublicTask,
  type TaskStatusTone,
} from '@/domain/collaborationSquare/types';
import { cn } from '@/utils/cn';
import { Bot } from 'lucide-react';

type BadgeTone = 'neutral' | 'primary' | 'success' | 'warning';

/** 任务状态语义徽标：文字 label + 语义 dot 双通道，不依赖颜色单一表达。
 *  `TaskStatusTone`（brand/info 无对应 Badge tone）映射为最近 Badge tone；区分度由 dot 语义色与文案保证。 */
const TASK_BADGE_TONE: Record<TaskStatusTone, BadgeTone> = {
  warning: 'warning',
  brand: 'primary',
  info: 'neutral',
  success: 'success',
};

/** 状态点缀的语义色（项目主题 token，非 Tailwind 调色板色）：warning/brand/info/success 四态可区分。 */
const TASK_DOT_CLASS: Record<TaskStatusTone, string> = {
  warning: 'bg-warning',
  brand: 'bg-brand',
  info: 'bg-info',
  success: 'bg-success',
};

/** 任务广场状态徽标（文字 + 语义 dot），可被任务卡与只读详情弹层复用。 */
export function TaskStatusBadge({ status }: { status: PlazaTaskStatus }) {
  const { label, tone } = getPublicTaskStatusPresentation(status);
  return (
    <Badge tone={TASK_BADGE_TONE[tone]} className="gap-1.5 shrink-0 whitespace-nowrap">
      <span aria-hidden className={cn('h-1.5 w-1.5 rounded-full', TASK_DOT_CLASS[tone])} />
      {label}
    </Badge>
  );
}

/** 任务发布者/认领者头像：语义色块 + Bot 图标，按 `size` 控制尺寸；可被任务卡与只读详情弹层复用。 */
export function TaskAvatar({ size }: { size: 'md' | 'sm' }) {
  return (
    <div
      aria-hidden
      className={cn(
        'flex shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary',
        size === 'md' ? 'h-10 w-10' : 'h-8 w-8',
      )}
    >
      <Bot aria-hidden className={size === 'md' ? 'h-5 w-5' : 'h-4 w-4'} />
    </div>
  );
}

/** 将 ISO 8601 时间字符串格式化为只读展示用的 `YYYY-MM-DD HH:MM`（24 小时制、零填充、本地时区、到分钟）。
 *  空/undefined/null/非法值 → 返回空串（保持不崩，不抛错）。 */
export function formatTaskDate(iso: string | undefined | null): string {
  if (!iso) return '';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '';
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(
    date.getMinutes(),
  )}`;
}

/** 任务执行结束阶段的时间行标签：已完成(completed)→「完成时间」，待验收(reviewing)→「开始验收时间」。
 *  值均来自 `completedAt`（后端 `relay_end_time`）；仅在该字段存在时由任务卡与详情弹层渲染。 */
export function getTaskEndTimeLabel(status: PlazaTaskStatus): '完成时间' | '开始验收时间' {
  return status === 'reviewing' ? '开始验收时间' : '完成时间';
}

export interface TaskCardProps {
  task: PublicTask;
  onOpenDetail: (task: PublicTask) => void;
}

export default function TaskCard({ task, onOpenDetail }: TaskCardProps) {
  const claimed = Boolean(task.claimedBotName);
  return (
    <Card className="flex h-full min-w-0 flex-col">
      <CardHeader className="gap-2 p-4 pb-0">
        <div className="flex min-w-0 items-center gap-2.5">
          <TaskAvatar size="md" />
          <div className="min-w-0 flex-1">
            <CardTitle className="truncate" title={task.name}>
              {task.name}
            </CardTitle>
            <p className="m-0 mt-1 truncate text-xs text-muted-foreground">
              发布者：{task.publisherName || task.publisherBotName}
            </p>
          </div>
        </div>
        <TaskStatusBadge status={task.status} />
      </CardHeader>
      <CardContent className="flex flex-1 flex-col gap-3 p-4 pt-3">
        {/* goal 即详情入口：hover/focus 弹 Tooltip 显示未截断的完整目标；点击/Enter/Space 打开只读详情弹层。 */}
        <TooltipProvider delayDuration={200}>
          <Tooltip>
            <TooltipTrigger asChild>
              <p
                role="button"
                tabIndex={0}
                onClick={() => onOpenDetail(task)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault();
                    onOpenDetail(task);
                  }
                }}
                className="m-0 line-clamp-2 cursor-pointer break-words text-sm leading-5 text-muted-foreground transition-colors hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded"
              >
                {task.goal || '暂无任务目标'}
              </p>
            </TooltipTrigger>
            {task.goal ? <TooltipContent className="max-w-xs break-words">{task.goal}</TooltipContent> : null}
          </Tooltip>
        </TooltipProvider>
        <div className="mt-auto border-t border-border pt-3">
          <p className="m-0 mb-2 text-xs font-medium text-muted-foreground">验收标准</p>
          {task.acceptanceCriteria.length > 0 ? (
            <div className="flex flex-wrap gap-1.5">
              {task.acceptanceCriteria.map((criterion) => (
                <Badge key={criterion}>{criterion}</Badge>
              ))}
            </div>
          ) : (
            <p className="m-0 text-xs text-muted-foreground">暂无验收标准</p>
          )}
        </div>
        {claimed && (
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <TaskAvatar size="sm" />
            <span className="min-w-0 truncate">{task.claimedBotName}</span>
            {task.claimedAt && <span className="shrink-0">认领于 {formatTaskDate(task.claimedAt)}</span>}
          </div>
        )}
        {task.completedAt && (
          <p className="m-0 text-xs text-muted-foreground">
            {getTaskEndTimeLabel(task.status)}：{formatTaskDate(task.completedAt)}
          </p>
        )}
      </CardContent>
      <CardFooter className="flex-wrap items-center justify-start gap-2 p-3">
        <div className="flex flex-row flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-muted-foreground">
          <span>发布于 {formatTaskDate(task.publishedAt)}</span>
          <span aria-hidden>·</span>
          <span>{claimed ? `认领：${task.claimedBotName}` : '等待认领'}</span>
        </div>
      </CardFooter>
    </Card>
  );
}
