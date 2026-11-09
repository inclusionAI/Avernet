import { TaskPanelV2 } from '@/assets/TaskPanel/v2/TaskPanelV2';
import { useLocalTaskPanelNav } from '@/assets/TaskPanel/v2/taskPanelNav';
import type { TaskArtifactView, TaskNodeView, TaskStatus, TaskView } from '@/assets/TaskPanel/types';
import { Button } from '@/components/ui/Button';
import { Drawer, DrawerContent, DrawerDescription, DrawerTitle } from '@/components/ui/Drawer';
import { helpStageParticipants } from '@/domain/communityHelp/model';
import type { HelpStageStatus, HelpTask, HelpTaskStatus } from '@/domain/communityHelp/types';
import { Play } from 'lucide-react';

const TASK_STATUS: Record<HelpTaskStatus, TaskStatus> = {
  pending: 'DEFINED',
  running: 'EXECUTING',
  attention: 'REVIEWING',
  done: 'DONE',
};

const NODE_STATUS: Record<HelpStageStatus, TaskNodeView['status']> = {
  pending: 'pending',
  running: 'running',
  attention: 'hung',
  done: 'done',
};

/** 将求助_new Mock 任务映射为任务副屏视图模型，直接复用 TaskPanel 的结构和样式。 */
function buildTaskView(task: HelpTask, boardName: string): TaskView {
  const nodes: TaskNodeView[] = task.stages.map((stage, index) => ({
    id: stage.id,
    name: stage.role,
    sequence: index + 1,
    status: NODE_STATUS[stage.status],
    present: NODE_STATUS[stage.status],
    executor: helpStageParticipants(task, stage),
    executorLabel: helpStageParticipants(task, stage),
    runMode: 'single_bot',
    output: stage.output,
    artifacts: task.artifacts
      .filter((artifact) => artifact.id === `${stage.id}-artifact`)
      .map<TaskArtifactView>((artifact) => ({
        id: artifact.id,
        name: artifact.name,
        type: stage.role === 'pr' ? 'other' : 'file',
        kind: stage.role === 'pr' ? 'code' : 'file',
        summary: artifact.content,
        updatedAt: task.createdAt,
        owner: 'Mock',
        ownerLabel: 'Mock',
        isFinalArtifact: stage.role === 'pr',
        codeRef:
          stage.role === 'pr'
            ? {
                codeBaseName: 'Avernet',
                codeBaseAddress: null,
                pullRequestUrl: null,
                description: artifact.content,
              }
            : null,
      })),
    hasSubTask: false,
    stepTraces: [],
    taskSpec: { title: stage.role, instruction: stage.instruction },
  }));

  const counts = {
    pending: task.stages.filter((stage) => stage.status === 'pending').length,
    running: task.stages.filter((stage) => stage.status === 'running').length,
    done: task.stages.filter((stage) => stage.status === 'done').length,
    hung: task.stages.filter((stage) => stage.status === 'attention').length,
  };

  return {
    id: task.id,
    name: task.title,
    description: task.description,
    goal: task.description,
    objective: task.description,
    acceptances: [],
    status: TASK_STATUS[task.status],
    taskType: 'dynamic',
    taskTypeLabel: `求助_new · ${boardName}`,
    sourceLabel: task.source,
    ownerBotName: boardName,
    ownerBotId: task.boardId,
    ownerUserId: 'mock-user',
    createdAt: task.createdAt,
    finishedAt: task.status === 'done' ? task.createdAt : null,
    loopRound: 1,
    needsAttention: task.status === 'attention',
    statusReason: task.stages.find((stage) => stage.status === 'attention')?.output ?? null,
    progress: {
      total: task.stages.length,
      ...counts,
      planning: 0,
      failed: 0,
      skipped: 0,
      percent: task.stages.length
        ? Math.round((counts.done / task.stages.length) * 100)
        : 0,
    },
    artifacts: [],
    nodes,
    dagNodes: nodes.map((node, index) => ({
      id: node.id,
      label: node.name,
      status: node.status,
      x: 0,
      y: index * 80,
      isCurrent: node.status === 'running' || node.status === 'hung',
      present: node.present,
      short: node.name,
    })),
    dagEdges: nodes.slice(1).map((node, index) => ({ from: nodes[index].id, to: node.id })),
  };
}

export function HelpTaskPanel({
  task,
  boardName,
  onClose,
  onAdvance,
}: {
  task: HelpTask;
  boardName: string;
  onClose: () => void;
  onAdvance: () => void;
}) {
  const nav = useLocalTaskPanelNav();
  const taskView = buildTaskView(task, boardName);
  return (
    <Drawer
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <DrawerContent size="lg" showClose={false} bodyClassName="h-full min-h-0 overflow-hidden p-0 pt-0">
        <DrawerTitle className="sr-only">{task.title}</DrawerTitle>
        <DrawerDescription className="sr-only">
          查看求助_new 协作进度与示例交付，不触发真实执行。
        </DrawerDescription>
        <div className="flex h-full min-h-0 flex-col">
          <div className="min-h-0 flex-1">
            <TaskPanelV2 task={taskView} nav={nav} apiBaseUrl="" onTogglePanel={onClose} />
          </div>
          <footer className="flex flex-wrap items-center justify-between gap-3 border-t border-border bg-card px-5 py-4">
            <span className="min-w-0 break-words text-xs text-muted-foreground">
              {boardName} · {task.source}
              {task.sourceRef ? ` · ${task.sourceRef}` : ''} · 仅演示，不执行真实任务
            </span>
            <Button
              variant="outline"
              disabled={task.status === 'done'}
              onClick={onAdvance}
              leftIcon={<Play className="size-3.5" aria-hidden />}
            >
              {task.status === 'done'
                ? '任务已完成'
                : task.status === 'attention'
                ? '模拟解除阻塞'
                : task.status === 'pending'
                ? '模拟开始协作'
                : '模拟推进下一阶段'}
            </Button>
          </footer>
        </div>
      </DrawerContent>
    </Drawer>
  );
}
