import type { TaskEscortParityContext } from '@/domain/taskEscortParity/types';
import { useTaskEscortEvolutionAccess } from '@/hooks/taskEscortParity/useTaskEscortEvolution';
import type { ReactNode } from 'react';
import { NoData, ReadState } from './ReadPrimitives';
export function EvolutionAccess({
  context,
  children,
}: {
  context: TaskEscortParityContext;
  children: (canEdit: boolean) => ReactNode;
}) {
  const access = useTaskEscortEvolutionAccess(context);
  if (context.error) return <ReadState error={context.error} />;
  if (!context.ready || !context.userId || !context.workflowId)
    return context.loading ? <ReadState loading /> : <NoData text="请选择可访问的真实工作流并确认登录身份" />;
  if (access.loading || access.error) return <ReadState {...access} />;
  if (!access.data) return null;
  if (!access.data.canView) return <ReadState error="403：当前账号无权查看此工作流" />;
  return <>{children(access.data.canEdit)}</>;
}
