import { Workflow } from 'lucide-react';
import { useMemo } from 'react';
import type { UseParticipantBindingResult } from '../../hooks/useParticipantBinding';
import { CollaborationFlowPreview } from './CollaborationFlowPreview';
import { buildCollaborationBindingViews } from './collaborationGraphLayout';

interface CollaborationFlowAsideProps {
  leaderOptions: Array<{ id: string; name: string; current?: boolean }>;
  binding: UseParticipantBindingResult;
}

export function CollaborationFlowAside({ leaderOptions, binding }: CollaborationFlowAsideProps) {
  const bindingViews = useMemo(
    () =>
      buildCollaborationBindingViews(
        binding.yamlValidation.participantDefinitions,
        Object.fromEntries(
          Object.entries(binding.participantBindings).map(([key, botId]) => [key, botId ? [botId] : []]),
        ),
        (botId) => leaderOptions.find((bot) => bot.id === botId)?.name,
      ),
    [binding.participantBindings, binding.yamlValidation.participantDefinitions, leaderOptions],
  );

  return (
    <aside
      aria-label="协作流程侧栏"
      className="absolute left-[calc(100%+1rem)] top-0 hidden h-full w-[400px] flex-col overflow-hidden rounded-lg border border-border bg-background px-4 py-6 shadow-lg xl:flex"
    >
      <div className="mb-3 flex items-center gap-2">
        <Workflow aria-hidden className="size-4 text-muted-foreground" />
        <span className="text-sm font-semibold text-foreground">协作流程</span>
      </div>
      <div data-testid="collaboration-flow-content" className="flex min-h-0 flex-1 items-center">
        {binding.graph ? (
          <CollaborationFlowPreview
            graph={binding.graph}
            initialNodes={binding.summary?.initial_nodes ?? []}
            bindingViews={bindingViews}
            highlightedBinding={binding.activeParticipantKey || undefined}
          />
        ) : (
          <div className="flex h-[400px] w-full items-center justify-center rounded-xl border border-dashed border-border bg-muted/30 text-center text-xs text-muted-foreground">
            此次校验未返回流程预览数据
          </div>
        )}
      </div>
    </aside>
  );
}
