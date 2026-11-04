import { Button } from '@/components/ui';
import { Modal, ModalContent, ModalTitle } from '@/components/ui/Modal';
import { Maximize2, Workflow } from 'lucide-react';
import { useMemo, useRef, useState } from 'react';
import type { UseParticipantBindingResult } from '../../hooks/useParticipantBinding';
import { CollaborationFlowPreview } from './CollaborationFlowPreview';
import { buildCollaborationBindingViews } from './collaborationGraphLayout';

interface CollaborationFlowAsideProps {
  leaderOptions: Array<{ id: string; name: string; current?: boolean }>;
  binding: UseParticipantBindingResult;
}

export function CollaborationFlowAside({ leaderOptions, binding }: CollaborationFlowAsideProps) {
  const [expanded, setExpanded] = useState(false);
  const anchorRef = useRef<HTMLDivElement>(null);
  const locateBinding = (key: string) => {
    binding.setActiveParticipantKey(key);
    setExpanded(false);
    requestAnimationFrame(() => {
      const panel = anchorRef.current?.closest('[role="dialog"]')?.querySelector('[data-role-binding-panel]');
      panel?.scrollIntoView?.({ block: 'nearest', behavior: 'smooth' });
      panel?.querySelector<HTMLButtonElement>('[aria-pressed="true"]')?.focus();
    });
  };
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

  const renderPreview = (onLocateBinding?: (key: string) => void) => binding.graph ? (
    <CollaborationFlowPreview
      graph={binding.graph}
      initialNodes={binding.summary?.initial_nodes ?? []}
      bindingViews={bindingViews}
      highlightedBinding={binding.activeParticipantKey || undefined}
      onSelectBinding={binding.setActiveParticipantKey}
      onLocateBinding={onLocateBinding}
    />
  ) : <div className="flex flex-1 items-center justify-center p-6 text-sm text-muted-foreground">此次校验未返回流程预览数据</div>;

  return <>
    <div ref={anchorRef} className="absolute right-14 top-4 xl:hidden">
      <Button variant="outline" size="sm" onClick={() => setExpanded(true)}><Workflow className="size-4" aria-hidden />流程预览</Button>
    </div>
    <aside
      aria-label="协作流程侧栏"
      className="absolute left-[calc(100%+1rem)] top-0 hidden h-full w-[480px] flex-col overflow-hidden rounded-lg border border-border bg-background shadow-lg xl:flex 2xl:w-[560px]"
    >
      <div className="flex h-16 shrink-0 items-center justify-between gap-3 border-b border-border px-4">
        <div className="flex items-center gap-2"><Workflow aria-hidden className="size-4 text-muted-foreground" /><span className="text-sm font-semibold text-foreground">协作流程</span></div>
        <Button variant="ghost" size="sm" onClick={() => setExpanded(true)}><Maximize2 className="size-4" aria-hidden />展开预览</Button>
      </div>
      <div data-testid="collaboration-flow-content" className="flex min-h-0 flex-1 flex-col">{renderPreview()}</div>
    </aside>
    <Modal open={expanded} onOpenChange={setExpanded}>
      <ModalContent size="full" closeLabel="关闭展开预览" aria-describedby={undefined}
        className="h-[calc(100dvh-2rem)] grid-rows-[auto_minmax(0,1fr)] gap-0 overflow-hidden p-0">
        <div className="border-b border-border px-5 py-4 pr-14"><ModalTitle>协作流程预览</ModalTitle></div>
        {renderPreview(locateBinding)}
      </ModalContent>
    </Modal>
  </>;
}
