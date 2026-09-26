import { getCapabilities } from '@/capabilities';
import {
  CreateBotTooltip,
  CreateBotTooltipContent,
  CreateBotTooltipProvider,
  CreateBotTooltipTrigger,
} from '@/components/BotWorkshop/CreateBotModal/CreateBotTooltip';
import { Button } from '@/components/ui/Button';
import type { WorkflowItem } from '@/services/botWorkshop/agentCodingLegacyService';
import { cn } from '@/utils/cn';
import { ExternalLink, Info } from 'lucide-react';

export interface WorkflowGroup {
  domain: string;
  displayName: string;
  items: WorkflowItem[];
}

interface WorkflowDropdownPanelProps {
  options: WorkflowItem[];
  groups: WorkflowGroup[];
  activeGroup?: WorkflowGroup;
  activeDomain: string | null;
  selectedPath?: string;
  disabled: boolean;
  onDomainChange: (domain: string) => void;
  onSelect: (workflow: WorkflowItem) => void;
}

function getWorkflowTitle(workflow: WorkflowItem): string {
  return workflow.title || workflow.name;
}

function getWorkflowDesc(workflow: WorkflowItem): string {
  return workflow.desc || workflow.description || '';
}

function WorkflowDescriptionTooltip({ description }: { description: string }) {
  return (
    <CreateBotTooltipProvider delayDuration={200}>
      <CreateBotTooltip>
        <CreateBotTooltipTrigger asChild>
          <span
            onMouseDown={(event) => event.preventDefault()}
            onClick={(event) => event.stopPropagation()}
            className="flex-shrink-0 rounded p-0.5 text-slate-300 opacity-0 transition-all group-hover/item:opacity-100 hover:bg-slate-100 hover:text-slate-500"
            aria-label="查看完整工作流描述"
          >
            <Info size={13} />
          </span>
        </CreateBotTooltipTrigger>
        <CreateBotTooltipContent
          side="top"
          align="end"
          className="max-w-[360px] select-text whitespace-pre-line break-words text-xs leading-relaxed"
          onPointerDown={(event) => event.stopPropagation()}
          onMouseDown={(event) => event.stopPropagation()}
          onClick={(event) => event.stopPropagation()}
        >
          {description}
        </CreateBotTooltipContent>
      </CreateBotTooltip>
    </CreateBotTooltipProvider>
  );
}

export function WorkflowDropdownPanel({
  options,
  groups,
  activeGroup,
  activeDomain,
  selectedPath,
  disabled,
  onDomainChange,
  onSelect,
}: WorkflowDropdownPanelProps) {
  const workflowRepositoryBaseUrl = getCapabilities().getAgentCodingInternalResources().value.workflowRepositoryBaseUrl;
  return (
    <div className="absolute z-50 mt-1 flex max-h-80 w-full overflow-hidden rounded-lg border border-slate-200 bg-background shadow-lg">
      {options.length === 0 ? (
        <div className="w-full px-3 py-2 text-center text-xs text-slate-400">暂无工作流</div>
      ) : (
        <>
          <div className="w-28 flex-shrink-0 overflow-y-auto border-r border-slate-200 bg-muted/40">
            {groups.map((group) => {
              const isActive = activeDomain === group.domain;
              return (
                <Button
                  key={group.domain}
                  type="button"
                  variant="ghost"
                  onClick={() => onDomainChange(group.domain)}
                  className={cn(
                    'h-auto min-h-0 w-full cursor-pointer justify-start rounded-none border-0 border-l-2 px-3 py-2 text-left text-[12px] font-normal shadow-none transition-colors',
                    isActive
                      ? 'border-l-2 border-l-primary bg-background text-slate-900'
                      : 'border-l-2 border-l-transparent hover:bg-muted',
                  )}
                >
                  <span
                    className={cn('line-clamp-1', isActive ? 'font-bold text-slate-900' : 'font-medium text-slate-600')}
                  >
                    {group.displayName}
                  </span>
                </Button>
              );
            })}
          </div>

          <div className="min-w-0 flex-1 overflow-y-auto">
            {activeGroup ? (
              activeGroup.items.map((workflow) => {
                const title = getWorkflowTitle(workflow);
                const desc = getWorkflowDesc(workflow);
                const isSelected = selectedPath === workflow.path;
                return (
                  <Button
                    key={workflow.path}
                    type="button"
                    variant="ghost"
                    onClick={() => onSelect(workflow)}
                    disabled={disabled}
                    className={cn(
                      'group/item h-auto min-h-0 w-full cursor-pointer justify-start rounded-none border-0 border-b border-slate-100 px-3 py-2 text-left font-normal shadow-none transition-colors hover:bg-muted/60',
                      'last:border-b-0',
                      isSelected && 'bg-primary/10',
                    )}
                  >
                    <div className="min-w-0">
                      <div className="flex items-start gap-2">
                        <span className="flex min-w-0 flex-1 flex-wrap items-center gap-1.5 text-[13px] font-medium text-foreground">
                          {title}
                          {workflow.tags?.map((tag) => (
                            <span
                              key={tag}
                              className="rounded bg-primary/10 px-1.5 py-0.5 text-[10px] font-medium leading-tight text-primary"
                            >
                              {tag}
                            </span>
                          ))}
                        </span>
                        {desc ? <WorkflowDescriptionTooltip description={desc} /> : null}
                        {workflowRepositoryBaseUrl ? (
                          <a
                            href={`${workflowRepositoryBaseUrl}/${workflow.path}`}
                            target="_blank"
                            rel="noopener noreferrer"
                            onClick={(event) => event.stopPropagation()}
                            className="flex-shrink-0 rounded p-0.5 text-slate-300 opacity-0 transition-all group-hover/item:opacity-100 hover:bg-slate-100 hover:text-slate-500"
                            title="查看工作流详情"
                          >
                            <ExternalLink size={13} />
                          </a>
                        ) : null}
                      </div>
                      {desc ? (
                        <span className="mt-0.5 line-clamp-2 text-[11px] text-muted-foreground">
                          {desc.replace(/\n/g, ' ')}
                        </span>
                      ) : null}
                    </div>
                  </Button>
                );
              })
            ) : (
              <div className="px-3 py-4 text-center text-xs text-slate-400">请选择分类</div>
            )}
          </div>
        </>
      )}
    </div>
  );
}
