// @asset-migrated: teamclaw 自研
/** ComposerCapabilitiesMenu —— Sender 左侧「通用协作能力」胶囊触发器 + 单行三项能力菜单 + 工作流子面板。
 *
 * dmore 实测（hd_f54eba11 index.html，2026-10-10）：
 * - 胶囊 179×28：Plus 圆钮 28×28(rx9999, icon 12px #09090B) + 竖分隔线 1×16(#EEEEEE→bg-border) +
 *   文案「通用协作能力」13px/400 #09090B + ChevronDown 12px #A1A1AA。
 * - 菜单 162×116 rx12：行 148×34 rx6，单行 icon 14px + 文案 13px #09090B，无 desc 副文案。
 * - 工作流子面板 240×236 rx12（无标题行）：搜索框 206×28 rx8（占位 12px #B9BEC5）+ 行 218×34 rx12（icon+13px）。
 */
import {
  Badge,
  Button,
  Popover,
  PopoverContent,
  PopoverTrigger,
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui';
import { WorkflowSubPanel, type WorkflowPanelItem } from '@/components/Workspace/TaskComposerMenu/WorkflowSubPanel';
import type { UseTaskExecutionResult, WorkflowSelection } from '@/hooks/useTaskExecution';
import { cn } from '@/utils/cn';
import { ChevronDown, FileUp, FolderOpen, ImageDown, Plus, Sparkles, Workflow, X } from 'lucide-react';
import React, { useEffect, useRef, useState } from 'react';

/** 功能开关。clawweb 内部环境 /api/workflows 已实测可用（restore-design-chat-page-b2-b3 design.md D4 回填），开闸。 */
const WORKFLOW_TASK_ENABLED = true;

export interface ComposerCapabilitiesMenuProps {
  execution: UseTaskExecutionResult;
  onUpload?: () => void;
  /** 添加图片回调（打开文件选择器）。单聊设计稿未含此项；群域传入时仍保留「添加图片」行。 */
  onAddImage?: () => void;
  /** 文件管理回调（单聊头部资源面板承接，见 chat-header-panels）。传入时保留「文件管理」行（群域）。 */
  onManageFiles?: () => void;
  enableWorkflow?: boolean;
  disabled?: boolean;
  disabledReason?: string | null;
  selectedWorkflow?: WorkflowSelection | null;
  pendingDynamic?: boolean;
  onWorkflowSelected?: (w: WorkflowSelection) => void;
  onDynamicSelected?: () => void;
  onClearSelection?: () => void;
  className?: string;
}

/** 菜单行：单行 34px（dmore 148×34 rx6，icon 14px + 13px 文案，无 desc）。 */
const MenuRow = React.forwardRef<
  HTMLButtonElement,
  {
    icon: React.ReactNode;
    label: string;
    onClick?: () => void;
    disabled?: boolean;
    onMouseEnter?: () => void;
    onMouseLeave?: () => void;
  }
>(({ icon, label, onClick, disabled, onMouseEnter, onMouseLeave }, ref) => (
  <Button
    ref={ref}
    variant="ghost"
    onClick={onClick}
    disabled={disabled}
    onMouseEnter={onMouseEnter}
    onMouseLeave={onMouseLeave}
    className="h-[34px] w-full justify-start gap-1 rounded-md px-2.5 text-left text-[13px] font-normal text-foreground"
  >
    <span aria-hidden="true" className="flex size-3.5 shrink-0 items-center justify-center">
      {icon}
    </span>
    <span className="min-w-0 flex-1 truncate">{label}</span>
  </Button>
));
MenuRow.displayName = 'MenuRow';

/** 选中态 chip：胶囊切换形态（dmore 选中帧），x 清除后恢复胶囊。 */
function SelectionChip({ label, onClear }: { label: string; onClear: () => void }) {
  return (
    <Badge tone="primary" className="gap-1 py-0.5 pr-1">
      <span className="max-w-[160px] truncate">{label}</span>
      <Button variant="ghost" size="icon" onClick={onClear} className="h-4 w-4" aria-label="取消选择">
        <X className="h-3 w-3" />
      </Button>
    </Badge>
  );
}

/** 菜单主体：单行三项（上传文件/动态任务/工作流任务）；工作流行 hover 延迟展开右侧子面板。 */
function MenuBody({
  onUpload,
  onAddImage,
  onManageFiles,
  onDynamic,
  onPickWorkflow,
  workflowEnabled,
  disabled,
  workflows,
  workflowsLoading,
  onWorkflowHover,
}: {
  onUpload?: () => void;
  /** 添加图片回调（打开文件选择器）。单聊设计稿未含此项；群域传入时仍保留「添加图片」行。 */
  onAddImage?: () => void;
  onManageFiles?: () => void;
  onDynamic?: () => void;
  onPickWorkflow?: (w: WorkflowPanelItem) => void;
  /** 工作流任务是否可点(功能开关 × enableWorkflow)。false 时只展示、屏蔽点击，不展开二级列表。 */
  workflowEnabled: boolean;
  disabled?: boolean;
  workflows: WorkflowPanelItem[];
  workflowsLoading: boolean;
  onWorkflowHover?: () => void;
}) {
  const [wfOpen, setWfOpen] = useState(false);
  const closeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  // 功能未开放或整体不可用时屏蔽：行禁用且 hover 不展开二级工作流列表。
  const workflowDisabled = disabled || !workflowEnabled;
  const enter = () => {
    if (closeTimer.current) clearTimeout(closeTimer.current);
    if (!workflowDisabled) {
      setWfOpen(true);
      onWorkflowHover?.();
    }
  };
  const leave = () => {
    closeTimer.current = setTimeout(() => setWfOpen(false), 120);
  };
  useEffect(
    () => () => {
      if (closeTimer.current) clearTimeout(closeTimer.current);
    },
    [],
  );

  return (
    <div className="flex flex-col">
      {/* artboard-020：「上传文件」行 hover tooltip（菜单行缺口注解）。MenuRow 为 forwardRef Button，可作 TooltipTrigger。 */}
      <TooltipProvider>
        <Tooltip>
          <TooltipTrigger asChild>
            <MenuRow
              icon={<FileUp className="size-3.5" />}
              label="上传文件"
              onClick={onUpload}
              disabled={!onUpload || disabled}
            />
          </TooltipTrigger>
          <TooltipContent>上传文件</TooltipContent>
        </Tooltip>
      </TooltipProvider>
      {/* 群域遗留能力位：单聊设计稿不含，仅当调用方传入回调时保留（行为零裁剪）。 */}
      {onAddImage && (
        <MenuRow icon={<ImageDown className="size-3.5" />} label="添加图片" onClick={onAddImage} disabled={disabled} />
      )}
      {onManageFiles && (
        <MenuRow
          icon={<FolderOpen className="size-3.5" />}
          label="文件管理"
          onClick={onManageFiles}
          disabled={disabled}
        />
      )}
      <MenuRow icon={<Sparkles className="size-3.5" />} label="动态任务" onClick={onDynamic} disabled={disabled} />
      <Popover open={workflowDisabled ? false : wfOpen} onOpenChange={setWfOpen}>
        <PopoverTrigger asChild>
          <MenuRow
            icon={<Workflow className="size-3.5" />}
            label="工作流任务"
            disabled={workflowDisabled}
            onMouseEnter={enter}
            onMouseLeave={leave}
          />
        </PopoverTrigger>
        <PopoverContent
          side="right"
          align="start"
          sideOffset={2}
          className="w-[240px] rounded-xl p-[10px]"
          onMouseEnter={enter}
          onMouseLeave={leave}
        >
          <WorkflowSubPanel
            workflows={workflows}
            loading={workflowsLoading}
            onPick={(w) => {
              setWfOpen(false);
              onPickWorkflow?.(w);
            }}
          />
        </PopoverContent>
      </Popover>
    </div>
  );
}

export function ComposerCapabilitiesMenu({
  execution,
  onUpload,
  onAddImage,
  onManageFiles,
  enableWorkflow = false,
  disabled,
  disabledReason,
  selectedWorkflow,
  pendingDynamic,
  onWorkflowSelected,
  onDynamicSelected,
  onClearSelection,
  className,
}: ComposerCapabilitiesMenuProps) {
  const [open, setOpen] = useState(false);
  const hasSelection = !!selectedWorkflow || pendingDynamic;
  const close = () => setOpen(false);
  const pickWorkflow = (w: WorkflowPanelItem) => {
    onWorkflowSelected?.(w);
    close();
  };
  const withClose = (fn?: () => void) =>
    fn
      ? () => {
          close();
          fn();
        }
      : undefined;

  return (
    <div className={cn('flex items-center gap-2', className)}>
      {hasSelection ? (
        <SelectionChip
          label={selectedWorkflow ? selectedWorkflow.title : '动态任务'}
          onClear={() => onClearSelection?.()}
        />
      ) : (
        <Popover open={open} onOpenChange={setOpen}>
          <PopoverTrigger asChild>
            {/* 胶囊触发器（dmore 179×28）：＋圆钮 + 竖分隔线 + 文案 + 下拉指示。
             * 总开关 disabled 只禁用子项并呈现 disabledReason，触发器保持可开（spec Scenario「不可会话态」）。 */}
            <Button
              variant="ghost"
              aria-label="通用协作能力"
              aria-haspopup="menu"
              aria-expanded={open}
              className="h-7 gap-0 px-0"
            >
              <span aria-hidden="true" className="flex size-7 items-center justify-center rounded-full">
                <Plus className="size-3" />
              </span>
              <span aria-hidden="true" className="mx-2.5 h-4 w-px bg-border" />
              <span className="text-[13px] font-normal leading-none text-foreground">通用协作能力</span>
              <ChevronDown aria-hidden="true" className="ml-1.5 size-3 text-muted-foreground" />
            </Button>
          </PopoverTrigger>
          <PopoverContent align="start" className="w-[162px] rounded-xl p-[7px]">
            <MenuBody
              onUpload={withClose(onUpload)}
              onAddImage={withClose(onAddImage)}
              onManageFiles={withClose(onManageFiles)}
              onDynamic={() => {
                onDynamicSelected?.();
                close();
              }}
              onPickWorkflow={enableWorkflow && WORKFLOW_TASK_ENABLED ? pickWorkflow : undefined}
              workflowEnabled={enableWorkflow && WORKFLOW_TASK_ENABLED}
              disabled={disabled}
              workflows={execution.workflows}
              workflowsLoading={execution.workflowsLoading}
              onWorkflowHover={() => void execution.loadWorkflows()}
            />
            {disabled && disabledReason && <p className="mt-1 px-2 text-xs text-destructive">{disabledReason}</p>}
          </PopoverContent>
        </Popover>
      )}
    </div>
  );
}
