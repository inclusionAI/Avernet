import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Empty } from '@/components/ui/Empty';
import { Modal, ModalContent, ModalDescription, ModalHeader, ModalTitle, ModalTrigger } from '@/components/ui/Modal';
import { managementSourceLabel, managementTime } from '@/domain/taskEscortParity/management';
import type { ManagementHistory as History, ManagementHistoryItem } from '@/domain/taskEscortParity/managementTypes';
import type { TaskEscortParityContext } from '@/domain/taskEscortParity/types';
import { useTaskEscortManagementResource } from '@/hooks/taskEscortParity/useTaskEscortManagement';
import { managementService } from '@/services/taskEscortParity/managementService';
import { cn } from '@/utils/cn';
import { useCallback, useState } from 'react';
import { DisabledWrite, Fields, ManagementSection, ReadState } from './ManagementPrimitives';

interface Props {
  context: TaskEscortParityContext;
  canEdit: boolean;
  resource: { data: History | null; loading: boolean; error: string | null; refresh: () => void };
}
function SnapshotDialog({
  context,
  item,
  mode,
}: {
  context: TaskEscortParityContext;
  item: ManagementHistoryItem;
  mode: 'version' | 'deploy';
}) {
  const [open, setOpen] = useState(false);
  const read = useCallback(
    (signal: AbortSignal) =>
      mode === 'version'
        ? managementService.version(context.workflowId, context.userId!, item.version, signal)
        : managementService.deploy(context.workflowId, context.userId!, item.deployNumber, signal),
    [context.workflowId, context.userId, item.version, item.deployNumber, mode],
  );
  const resource = useTaskEscortManagementResource(
    context,
    `snapshot:${mode}:${item.version}:${item.deployNumber}`,
    read,
    open,
  );
  return (
    <Modal open={open} onOpenChange={setOpen}>
      <ModalTrigger asChild>
        <Button variant="outline" size="sm">
          {mode === 'version' ? '版本快照' : '部署快照'}
        </Button>
      </ModalTrigger>
      <ModalContent size="xl">
        <ModalHeader>
          <ModalTitle>
            {mode === 'version' ? '版本快照' : '部署快照'} · v{item.version} · #{item.deployNumber}
          </ModalTitle>
          <ModalDescription>真实只读快照；敏感配置已隐藏。不支持保存或回滚。</ModalDescription>
        </ModalHeader>
        <ReadState resource={resource} />
        {resource.data && (
          <>
            <Fields
              fields={[
                ['工作流', resource.data.workflowId],
                ['版本', resource.data.version],
                ['部署', resource.data.deployNumber],
                ['动作', resource.data.action],
                ['备注', resource.data.note],
                ['创建时间', managementTime(resource.data.createdAtMs)],
              ]}
            />
            <pre className="max-h-96 overflow-auto whitespace-pre-wrap break-all rounded-lg bg-muted p-4 font-mono text-xs">
              {resource.data.specJson}
            </pre>
          </>
        )}
      </ModalContent>
    </Modal>
  );
}

export function ManagementHistory({ context, resource, canEdit }: Props) {
  const [expanded, setExpanded] = useState(false);
  const data = resource.data;
  return (
    <ManagementSection title="版本历史">
      <ReadState resource={resource} />
      {data && (
        <>
          {!data.history.length && <Empty compact title="暂无版本历史" />}
          <p className="text-muted-foreground">
            最近最多 20 条部署记录 · 上海时间
            {data.active ? ` · 当前激活 deploy #${data.active.deployNumber}` : ' · 暂无激活版本'}
          </p>
          <ul className="space-y-2">
            {(expanded ? data.history : data.history.slice(0, 5)).map((item) => {
              const current = data.active?.deployNumber === item.deployNumber;
              return (
                <li
                  key={item.deployNumber}
                  className={cn(
                    'min-w-0 space-y-2 rounded-lg border p-3',
                    current ? 'border-primary bg-primary/5' : 'border-border bg-card',
                  )}
                >
                  <header className="flex flex-wrap items-center gap-2">
                    <h3 className="text-sm font-medium">v{item.version}</h3>
                    {current && <Badge tone="primary">当前</Badge>}
                    <span className="font-medium">deploy #{item.deployNumber}</span>
                    <Badge tone="neutral">{item.action}</Badge>
                    {item.tagName && <span className="break-words [overflow-wrap:anywhere]">{item.tagName}</span>}
                  </header>
                  <p className="break-words text-muted-foreground [overflow-wrap:anywhere]">{item.note || '无备注'}</p>
                  <p className="break-words text-muted-foreground [overflow-wrap:anywhere]">
                    {managementTime(item.createdAtMs)} · {managementSourceLabel(item.source)}
                  </p>
                  {item.source && (
                    <details>
                      <summary className="cursor-pointer text-primary">发布来源详情</summary>
                      <Fields
                        fields={[
                          ['来源部署', item.source.deployNumber],
                          ['基线部署', item.source.baselineDeployNumber],
                          ['优化任务', item.source.taskId],
                          ['修订', item.source.revision],
                          ['自动化轮次', item.source.automationRoundId],
                          ['发布人', item.source.actorId],
                          ['首次生效', managementTime(item.source.effectiveAtMs)],
                          ['恢复自部署', item.source.restoredFromDeployNumber],
                          [
                            '原始来源',
                            item.source.restoredSource ? managementSourceLabel(item.source.restoredSource) : null,
                          ],
                        ]}
                      />
                    </details>
                  )}
                  <div className="flex flex-wrap items-center gap-2">
                    <SnapshotDialog context={context} item={item} mode="version" />
                    <SnapshotDialog context={context} item={item} mode="deploy" />
                    {canEdit && !current && <DisabledWrite>回滚</DisabledWrite>}
                  </div>
                </li>
              );
            })}
          </ul>
          {data.history.length > 5 && (
            <Button
              variant="outline"
              size="sm"
              className="w-full border-dashed bg-muted/30 font-normal text-muted-foreground"
              aria-expanded={expanded}
              onClick={() => setExpanded((value) => !value)}
            >
              {expanded ? '收起历史版本' : `展开其余 ${data.history.length - 5} 个版本`}
            </Button>
          )}
        </>
      )}
    </ManagementSection>
  );
}
