/** Attachment/task rows and their nested workflow popover. */
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui';
import { Boxes, FileUp, FolderOpen, ImageDown, Sparkles, Workflow } from 'lucide-react';
import { useEffect, useRef, useState, type ReactNode } from 'react';
import { MenuRow } from './ComposerMenuPrimitives';
import { WorkflowSubPanel, type WorkflowPanelItem } from './WorkflowSubPanel';

/** 菜单主体：上传文件/任务/工作流任务/Agent Compute 并列单行；工作流行 hover 延迟展开右侧子面板。 */
export function ComposerCapabilitiesMenuBody({
  onUpload,
  onAddImage,
  onManageFiles,
  onDynamic,
  onAgentCompute,
  onEvolution,
  onPickWorkflow,
  dynamicTaskEnabled,
  workflowEnabled,
  disabled,
  workflows,
  workflowsLoading,
  onWorkflowHover,
  showTaskEntries = true,
  extraMenuRows,
}: {
  onUpload?: () => void;
  /** 添加图片回调（打开文件选择器）。单聊设计稿未含此项；群域传入时仍保留「添加图片」行。 */
  onAddImage?: () => void;
  onManageFiles?: () => void;
  onDynamic?: () => void;
  /** Agent Compute 入口：与「任务」「工作流任务」并列；未传入时不渲染该行。 */
  onAgentCompute?: () => void;
  onEvolution?: () => void;
  onPickWorkflow?: (w: WorkflowPanelItem) => void;
  /** 是否安装了 task-intake；未安装时「任务」仅可见、不可选择。 */
  dynamicTaskEnabled: boolean;
  /** 工作流任务是否可点(功能开关 × enableWorkflow)。false 时只展示、屏蔽点击，不展开二级列表。 */
  workflowEnabled: boolean;
  disabled?: boolean;
  workflows: WorkflowPanelItem[];
  workflowsLoading: boolean;
  onWorkflowHover?: () => void;
  showTaskEntries?: boolean;
  /** 平台注入的额外菜单行（capability 覆盖式）：渲染在任务/工作流行之后，行样式由注入方负责。 */
  extraMenuRows?: ReactNode;
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
      {showTaskEntries ? (
        <>
          <MenuRow
            icon={<Sparkles className="size-3.5" />}
            label="任务"
            onClick={onDynamic}
            disabled={disabled || !dynamicTaskEnabled}
          />
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
        </>
      ) : null}
      {/* Agent Compute：与「任务」「工作流任务」并列的入口；入口说明收进悬停提示（PRD v1.3 §1）。 */}
      {onAgentCompute ? (
        <TooltipProvider>
          <Tooltip>
            <TooltipTrigger asChild>
              <MenuRow
                icon={<Boxes className="size-3.5" />}
                label="计算任务"
                onClick={onAgentCompute}
                disabled={disabled}
              />
            </TooltipTrigger>
            <TooltipContent>根据工作目标，定义执行 Agent，启动多个容器协同批量计算</TooltipContent>
          </Tooltip>
        </TooltipProvider>
      ) : null}
      {/* 平台注入的额外菜单行（capability 覆盖式，2026-11-04）：渲染在任务/工作流之后，
          行样式与视觉一致性由注入方负责（复用 MenuRow）。 */}
      {onEvolution ? (
        <MenuRow
          icon={<Sparkles className="size-3.5" />}
          label="RSI任务"
          onClick={onEvolution}
          disabled={disabled || !showTaskEntries}
        />
      ) : null}
      {extraMenuRows}
    </div>
  );
}
