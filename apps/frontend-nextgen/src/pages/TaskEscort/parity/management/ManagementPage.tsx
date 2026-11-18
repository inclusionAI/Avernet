import { Empty } from '@/components/ui/Empty';
import type { TaskEscortParityContext } from '@/domain/taskEscortParity/types';
import { useTaskEscortManagement } from '@/hooks/taskEscortParity/useTaskEscortManagement';
import { ManagementHistory } from './ManagementHistory';
import { DisabledWrite, ReadState } from './ManagementPrimitives';
import { ManagementSettings } from './ManagementSettings';

function ManagementContent({ context }: { context: TaskEscortParityContext }) {
  const { access, history, canEdit } = useTaskEscortManagement(context);
  if (!context.ready || !context.userId || !context.workflowId)
    return <Empty compact title="请选择可访问的工作流并确认登录身份" />;
  return (
    <div className="min-w-0 space-y-4 text-xs text-foreground">
      <ReadState resource={access} />
      {access.data?.canView === false && <Empty compact title="当前账号无权查看此工作流" />}
      {access.data?.canView === true && (
        <>
          <ManagementHistory context={context} resource={history} canEdit={canEdit} />
          {canEdit ? (
            <ManagementSettings context={context} />
          ) : (
            <p className="rounded-lg border border-border bg-muted p-4 text-muted-foreground">
              当前账号拥有查看权限；配置、回滚和删除需要工作流编辑权限。
            </p>
          )}
          {canEdit && (
            <details className="rounded-xl border border-border bg-card">
              <summary className="cursor-pointer p-4 font-medium">高级操作</summary>
              <div className="space-y-3 border-t border-border p-4">
                <p>删除工作流后无法恢复，请确认已不再需要。</p>
                <DisabledWrite destructive>删除工作流</DisabledWrite>
              </div>
            </details>
          )}
        </>
      )}
    </div>
  );
}
export function ManagementPage({ context }: { context: TaskEscortParityContext }) {
  // Remount nested selections on every actor/workflow/space boundary, not just after an effect.
  return (
    <ManagementContent
      key={`${context.scopeKey}:${context.userId}:${context.workflowId}:${context.ready}`}
      context={context}
    />
  );
}
export default ManagementPage;
