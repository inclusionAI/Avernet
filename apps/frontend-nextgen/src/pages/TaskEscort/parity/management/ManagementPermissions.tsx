import { Empty } from '@/components/ui/Empty';
import type { TaskEscortParityContext } from '@/domain/taskEscortParity/types';
import { useTaskEscortManagementResource } from '@/hooks/taskEscortParity/useTaskEscortManagement';
import { managementService } from '@/services/taskEscortParity/managementService';
import { useCallback } from 'react';
import { DisabledWrite, Fields, ReadState, yesNo } from './ManagementPrimitives';

export function ManagementPermissions({ context }: { context: TaskEscortParityContext }) {
  const read = useCallback(
    (signal: AbortSignal) => managementService.permissions(context.workflowId, context.userId!, signal),
    [context.workflowId, context.userId],
  );
  const resource = useTaskEscortManagementResource(context, 'permissions', read);
  return (
    <section aria-label="Bot 权限" className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h3 className="text-sm font-medium">Bot 权限</h3>
        <DisabledWrite>添加权限</DisabledWrite>
      </div>
      <p className="text-muted-foreground">
        权限按 owner 与 bot 标识显示；* 为所有人 / 所有 bot。不会推断当前账号为管理员。
      </p>
      <ReadState resource={resource} />
      {resource.data?.length === 0 && <Empty compact title="暂无 Bot 权限" />}
      <ul className="space-y-3">
        {resource.data?.map((p) => (
          <li key={p.id} className="space-y-3 rounded-lg border border-border p-3">
            <Fields
              fields={[
                ['Owner', p.botOwnerId === '*' ? '所有人' : p.botOwnerId],
                ['Bot', p.botId === '*' ? '所有 bot' : p.botId ?? 'owner 级'],
                ['查看', yesNo(p.canView)],
                ['执行', yesNo(p.canExecute)],
                ['编辑', yesNo(p.canEdit)],
              ]}
            />
            <DisabledWrite destructive>删除权限</DisabledWrite>
          </li>
        ))}
      </ul>
    </section>
  );
}
export function ManagementFacades({ context }: { context: TaskEscortParityContext }) {
  const read = useCallback(
    (signal: AbortSignal) => managementService.facades(context.workflowId, context.userId!, signal),
    [context.workflowId, context.userId],
  );
  const resource = useTaskEscortManagementResource(context, 'facades', read);
  return (
    <section aria-label="Facade 命令绑定" className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h3 className="text-sm font-medium">Facade 命令绑定</h3>
        <DisabledWrite>绑定命令</DisabledWrite>
      </div>
      <ReadState resource={resource} />
      {resource.data?.length === 0 && <Empty compact title="暂无命令绑定" />}
      <ul className="space-y-3">
        {resource.data?.map((f) => (
          <li key={f.command} className="space-y-3 rounded-lg border border-border p-3">
            <Fields
              fields={[
                ['命令', f.command],
                ['备注', f.remark],
                ['工作流', f.workflowId],
                ['Pack', f.packId],
              ]}
            />
            <div className="flex flex-wrap gap-3">
              <DisabledWrite>更新备注</DisabledWrite>
              <DisabledWrite destructive>解绑命令</DisabledWrite>
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}
