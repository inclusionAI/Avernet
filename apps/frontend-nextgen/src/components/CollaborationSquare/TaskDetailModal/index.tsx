import {
  formatTaskDate,
  getTaskEndTimeLabel,
  TaskAvatar,
  TaskStatusBadge,
} from '@/components/CollaborationSquare/TaskCard';
import { Modal, ModalContent, ModalDescription, ModalHeader, ModalTitle } from '@/components/ui/Modal';
import { Skeleton } from '@/components/ui/Skeleton';
import { getPublicTaskStatusPresentation, type PublicTask } from '@/domain/collaborationSquare/types';
import type { ReactNode } from 'react';

/** 只读详情弹层关闭按钮的可访问名称。 */
const TASK_DETAIL_MODAL_CLOSE_LABEL = '关闭任务详情';

/** 详情区属性行：小标签 + 内容，仅展示，无操作。 */
function DetailRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-1">
      <p className="m-0 text-xs font-medium text-muted-foreground">{label}</p>
      <div className="text-sm text-foreground">{children}</div>
    </div>
  );
}

export interface TaskDetailModalProps {
  /** 受控开关：由调用方 `Boolean(selectedTaskId)` 驱动。 */
  open: boolean;
  /** 任务详情；加载中或空态时可为 null。 */
  task: PublicTask | null;
  /** 详情加载态：true 时内容区渲染骨架占位。 */
  loading: boolean;
  /** 关闭回调：遮罩点击、Escape、关闭按钮均触发。 */
  onClose: () => void;
}

/**
 * 任务广场只读详情弹层。纯展示：完整任务目标、全部验收标准、发布/认领/完成时间线与状态。
 * 弹层内不含任何写操作按钮（认领/提交/对话/跳转），仅提供关闭途径。状态以文字 + 语义徽标双通道呈现。
 */
export function TaskDetailModal({ open, task, loading, onClose }: TaskDetailModalProps) {
  const statusLabel = task ? getPublicTaskStatusPresentation(task.status).label : '';
  return (
    <Modal open={open} onOpenChange={(next) => !next && onClose()}>
      <ModalContent size="lg" closeLabel={TASK_DETAIL_MODAL_CLOSE_LABEL}>
        <ModalHeader>
          <div className="flex flex-wrap items-center gap-2">
            <ModalTitle>{task ? task.name : '任务详情'}</ModalTitle>
            {task && <TaskStatusBadge status={task.status} />}
          </div>
          <ModalDescription>
            只读详情，展示任务目标、验收标准、发布认领时间线、当前状态与输出内容，不提供任何写操作。
          </ModalDescription>
        </ModalHeader>

        {loading ? (
          <div aria-label="正在加载任务详情" className="space-y-3">
            <Skeleton.Line />
            <Skeleton.Line className="w-3/4" />
            <Skeleton.ListItem />
          </div>
        ) : task ? (
          <div className="space-y-4 text-sm">
            <DetailRow label="任务目标">
              <p className="m-0 break-words leading-6 text-foreground">{task.goal}</p>
            </DetailRow>
            <DetailRow label="验收标准">
              {task.acceptanceCriteria.length > 0 ? (
                <ul className="m-0 list-disc space-y-1 pl-5">
                  {task.acceptanceCriteria.map((criterion) => (
                    <li key={criterion} className="break-words leading-6 text-foreground">
                      {criterion}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="m-0 text-muted-foreground">暂无验收标准</p>
              )}
            </DetailRow>
            <DetailRow label="发布者">
              <div className="flex items-center gap-2.5">
                <TaskAvatar size="md" />
                <div className="min-w-0">
                  <p className="m-0 break-words font-medium text-foreground">
                    {task.publisher
                      ? `${task.publisher}${task.publisherName ? `（${task.publisherName}）` : ''}`
                      : task.publisherBotName ?? '未公开'}
                  </p>
                  <p className="m-0 mt-0.5 text-xs text-muted-foreground">发布于 {formatTaskDate(task.publishedAt)}</p>
                </div>
              </div>
            </DetailRow>
            {task.claimedBotName && (
              <DetailRow label="认领">
                <div className="flex items-center gap-2.5">
                  <TaskAvatar size="md" />
                  <div className="min-w-0">
                    <p className="m-0 font-medium text-foreground">{task.claimedBotName}</p>
                    {task.claimedAt && (
                      <p className="m-0 mt-0.5 text-xs text-muted-foreground">
                        认领于 {formatTaskDate(task.claimedAt)}
                      </p>
                    )}
                  </div>
                </div>
              </DetailRow>
            )}
            {task.completedAt && (
              <DetailRow label={getTaskEndTimeLabel(task.status)}>
                <p className="m-0 text-foreground">{formatTaskDate(task.completedAt)}</p>
              </DetailRow>
            )}
            <DetailRow label="当前状态">
              <p className="m-0 text-foreground">{statusLabel}</p>
            </DetailRow>
            {task.output && (
              <DetailRow label="输出内容">
                <p className="m-0 break-words whitespace-pre-wrap leading-6 text-foreground">{task.output}</p>
              </DetailRow>
            )}
          </div>
        ) : (
          <p className="m-0 text-sm text-muted-foreground">任务详情暂不可用。</p>
        )}
      </ModalContent>
    </Modal>
  );
}
