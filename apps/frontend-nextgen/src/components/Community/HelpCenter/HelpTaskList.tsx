import { Button } from '@/components/ui/Button';
import { DataTable, type DataTableColumn } from '@/components/ui/DataTable';
import { helpStageParticipants } from '@/domain/communityHelp/model';
import type { HelpTask } from '@/domain/communityHelp/types';
import { ArrowUpRight } from 'lucide-react';
import { HelpProgress, HelpStatusBadge } from './HelpTaskCard';
import { HelpTaskSourceInfo } from './HelpTaskSourceInfo';

export function HelpTaskList({ tasks, onOpen }: { tasks: HelpTask[]; onOpen: (id: string) => void }) {
  const columns: DataTableColumn<HelpTask>[] = [
    {
      id: 'task',
      header: '任务',
      className: 'py-4',
      cell: (task) => (
        <div className="min-w-0 space-y-1.5">
          <Button
            variant="link"
            onClick={() => onOpen(task.id)}
            className="h-auto w-full justify-start whitespace-normal p-0 text-left text-sm font-semibold leading-6 text-foreground no-underline hover:text-primary"
          >
            <span className="min-w-0 break-words">{task.title}</span>
          </Button>
          <p className="line-clamp-1 break-words text-xs leading-5 text-muted-foreground">{task.description}</p>
        </div>
      ),
    },
    {
      id: 'source',
      header: '来源',
      width: 'w-36',
      cell: (task) => (
        <div className="space-y-1">
          <p className="text-xs">{task.source}</p>
          {task.source !== '手动输入' && (
            <p className="break-all text-xs text-muted-foreground">{task.sourceRef || '未填写编号'}</p>
          )}
          <HelpTaskSourceInfo task={task} />
        </div>
      ),
    },
    {
      id: 'status',
      header: '状态',
      width: 'w-28',
      cell: (task) => <HelpStatusBadge status={task.status} />,
    },
    {
      id: 'progress',
      header: '阶段进度',
      width: 'w-44',
      cell: (task) => <HelpProgress task={task} />,
    },
    {
      id: 'stage',
      header: '当前阶段 / Bot',
      width: 'w-40',
      cell: (task) => {
        const current = task.stages.find((stage) => stage.status !== 'done');
        return current ? (
          <div className="space-y-1 text-xs">
            <p>{current.role}</p>
            <p className="break-words leading-5 text-muted-foreground">{helpStageParticipants(task, current)}</p>
          </div>
        ) : (
          <div className="space-y-1 text-xs">
            <p className="text-success">所有协作阶段已完成</p>
            {task.collaborationMode === 'open' && <p className="text-muted-foreground">开放协作 · 不限定特定 Bot</p>}
          </div>
        );
      },
    },
    {
      id: 'actions',
      header: '操作',
      width: 'w-28',
      align: 'end',
      cell: (task) => (
        <Button
          variant="ghost"
          size="sm"
          className="px-0 text-primary"
          aria-label={`查看${task.title}的进度`}
          onClick={() => onOpen(task.id)}
          rightIcon={<ArrowUpRight className="size-3.5" aria-hidden />}
        >
          查看进度
        </Button>
      ),
    },
  ];

  return (
    <div className="min-w-0 overflow-hidden rounded-xl border border-border bg-card">
      <DataTable
        columns={columns}
        rows={tasks}
        getRowKey={(task) => task.id}
        ariaLabel="求助任务列表"
        rowHeight="h-20"
        minWidth="min-w-[960px]"
      />
    </div>
  );
}
