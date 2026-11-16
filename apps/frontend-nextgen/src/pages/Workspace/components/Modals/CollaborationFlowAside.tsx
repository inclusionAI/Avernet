import { Button } from '@/components/ui';
import { Modal, ModalContent, ModalTitle } from '@/components/ui/Modal';
import { ChevronLeft, ChevronRight, Maximize2, Workflow } from 'lucide-react';
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
  ) : <div className="flex flex-1 items-center justify-center p-6 text-xs text-muted-foreground">此次校验未返回流程预览数据</div>;

  return <>
    <div ref={anchorRef} className="absolute right-2 top-1/2 z-10 -translate-y-1/2 xl:hidden">
      <Button variant="outline" aria-haspopup="dialog" aria-expanded={expanded} onClick={() => setExpanded(true)}
        className="h-28 w-10 flex-col gap-2 rounded-xl border-primary/20 bg-background/60 px-0 text-primary shadow-md backdrop-blur-sm hover:bg-background/90">
        <ChevronRight className="size-5" aria-hidden /><span className="text-xs tracking-[0.2em] [writing-mode:vertical-rl]">流程预览</span>
      </Button>
    </div>
    <aside
      aria-label="协作流程侧栏"
      data-flow-aside
      className="hidden min-h-0 min-w-0 flex-col overflow-hidden border-l border-border bg-muted/20 xl:col-start-2 xl:row-start-2 xl:flex"
    >
      <div className="flex h-14 shrink-0 items-center justify-between gap-3 border-b border-border px-5">
        <div className="flex items-center gap-2"><Workflow aria-hidden className="size-4 text-muted-foreground" /><span className="text-xs font-semibold text-foreground">协作流程</span></div>
        <Button variant="ghost" size="sm" onClick={() => setExpanded(true)}><Maximize2 className="size-4" aria-hidden />展开预览</Button>
      </div>
      <div data-testid="collaboration-flow-content" className="flex min-h-0 flex-1 flex-col">{renderPreview()}</div>
    </aside>
    <Modal open={expanded} onOpenChange={setExpanded}>
      <ModalContent size="full" closeLabel="关闭展开预览" aria-describedby={undefined}
        className="h-dvh max-h-dvh w-full max-w-full grid-rows-[auto_minmax(0,1fr)] gap-0 overflow-hidden rounded-none p-0 sm:h-[calc(100dvh-3rem)] sm:w-[calc(100vw-3rem)] sm:rounded-2xl">
        <div className="border-b border-border px-5 py-4 pr-14"><ModalTitle>协作流程预览</ModalTitle></div>
        {renderPreview(locateBinding)}
        <Button variant="outline" onClick={() => setExpanded(false)}
          className="absolute left-2 top-1/2 z-10 h-28 w-10 -translate-y-1/2 flex-col gap-2 rounded-xl border-primary/20 bg-background/60 px-0 text-primary shadow-md backdrop-blur-sm hover:bg-background/90">
          <ChevronLeft className="size-5" aria-hidden /><span className="text-xs tracking-[0.2em] [writing-mode:vertical-rl]">返回配置</span>
        </Button>
      </ModalContent>
    </Modal>
  </>;
}
