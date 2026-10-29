// @asset-migrated: teamclaw 自研
/** ComposerCapabilitiesMenu —— Sender 左侧「＋ | 通用协作能力」双区胶囊。
 *
 * 胶囊拆区（add-composer-capability-set-viewer，2026-10-14）：DMore 179×28 胶囊保持原视觉，
 * 以竖分隔线为界拆为两个独立触发区——
 * - ＋ 圆钮（aria「添加附件」）：打开原混排菜单（上传文件/添加图片/文件管理 + 任务/工作流任务），行为不变；
 * - 「通用协作能力 ⌄」文案区：仅当调用方装配 capabilitySets（单聊 我的/团队 Bot）时渲染，
 *   打开只读能力集查看（CapabilitySetViewer：已激活集 + 【查看明细】Skills/MCP 子面板）。
 * 好友 Bot 会话与协作群不传 capabilitySets → 文案区不渲染，＋ 区独立保留。
 *
 * 多胶囊叠加（2026-10-12 语义修正）：选中项不再取代胶囊触发器——胶囊常驻，
 * 选中项以 chip 列表在胶囊右侧并列追加，可继续点击添加；每 chip 独立 x 清除。
 * 叠加规则：同一工作流按 workflowId 去重、动态任务全场至多一个（见 useTaskExecution）。
 *
 * dmore 实测（hd_f54eba11 index.html，2026-10-10）：
 * - 胶囊 179×28：Plus 圆钮 28×28(rx9999, icon 12px #09090B) + 竖分隔线 1×16(#EEEEEE→bg-border) +
 *   文案「通用协作能力」13px/400 #09090B + ChevronDown 12px #A1A1AA。
 * - 菜单 162×116 rx12：行 148×34 rx6，单行 icon 14px + 文案 13px #09090B，无 desc 副文案。
 * - 工作流子面板 240×236 rx12（无标题行）：搜索框 206×28 rx8（占位 12px #B9BEC5）+ 行 218×34 rx12（icon+13px）。
 */
import {
  Button,
  Popover,
  PopoverContent,
  PopoverTrigger,
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui';
import { CapabilitySetViewer } from '@/components/Workspace/CapabilitySetViewer';
import { MenuRow, SelectionChip } from '@/components/Workspace/TaskComposerMenu/ComposerMenuPrimitives';
import { WorkflowSubPanel, type WorkflowPanelItem } from '@/components/Workspace/TaskComposerMenu/WorkflowSubPanel';
import { composerSelectionKey } from '@/services/tasks/taskMapper';
import { cn } from '@/utils/cn';
import { ChevronDown, FileUp, FolderOpen, ImageDown, Plus, Sparkles, Workflow } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';

/** 功能开关。clawweb 内部环境 /api/workflows 已实测可用（restore-design-chat-page-b2-b3 design.md D4 回填），开闸。 */
const WORKFLOW_TASK_ENABLED = true;

import type { ComposerCapabilitiesMenuProps } from './composerCapabilitiesMenuTypes';

export type { ComposerCapabilitiesMenuProps } from './composerCapabilitiesMenuTypes';


/** 菜单主体：单行三项（上传文件/任务/工作流任务）；工作流行 hover 延迟展开右侧子面板。 */
function MenuBody({
  onUpload,
  onAddImage,
  onManageFiles,
  onDynamic,
  onPickWorkflow,
  dynamicTaskEnabled,
  workflowEnabled,
  disabled,
  workflows,
  workflowsLoading,
  onWorkflowHover,
  showTaskEntries = true,
}: {
  onUpload?: () => void;
  /** 添加图片回调（打开文件选择器）。单聊设计稿未含此项；群域传入时仍保留「添加图片」行。 */
  onAddImage?: () => void;
  onManageFiles?: () => void;
  onDynamic?: () => void;
  onPickWorkflow?: (w: WorkflowPanelItem) => void;
  /** 是否安装了 task-intent-recognition；未安装时「任务」仅可见、不可选择。 */
  dynamicTaskEnabled: boolean;
  /** 工作流任务是否可点(功能开关 × enableWorkflow)。false 时只展示、屏蔽点击，不展开二级列表。 */
  workflowEnabled: boolean;
  disabled?: boolean;
  workflows: WorkflowPanelItem[];
  workflowsLoading: boolean;
  onWorkflowHover?: () => void;
  showTaskEntries?: boolean;
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
  selections,
  onWorkflowSelected,
  onDynamicSelected,
  onRemoveSelection,
  capabilitySets,
  showTaskEntries = true,
  className,
  onOpened,
}: ComposerCapabilitiesMenuProps) {
  const [open, setOpen] = useState(false);
  const close = () => setOpen(false);
  const [capabilityOpen, setCapabilityOpen] = useState(false);
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
      <div className="flex items-center">
        {/* ＋区（aria「添加附件」）：打开原混排菜单（附件行 + 任务行），行为与拆分前完全一致。
         * 胶囊常驻（多胶囊语义）：有选中项时 chips 并列其右，仍可继续点击添加。
         * 总开关 disabled 只禁用子项并呈现 disabledReason，触发器保持可开（spec Scenario「不可会话态」）。 */}
        <Popover
        open={open}
        onOpenChange={(next) => {
          setOpen(next);
          if (next) onOpened?.();
        }}
      >
          <PopoverTrigger asChild>
            <Button
              variant="ghost"
              aria-label="添加附件"
              aria-haspopup="menu"
              aria-expanded={open}
              className="size-7 rounded-full p-0"
            >
              <Plus className="size-3" />
            </Button>
          </PopoverTrigger>
          <PopoverContent align="start" className="w-[162px] rounded-xl p-[7px]">
            <MenuBody
              onUpload={withClose(onUpload)}
              onAddImage={withClose(onAddImage)}
              onManageFiles={withClose(onManageFiles)}
              onDynamic={execution ? () => {
                onDynamicSelected?.();
                close();
              } : undefined}
              onPickWorkflow={execution && enableWorkflow && WORKFLOW_TASK_ENABLED ? pickWorkflow : undefined}
              dynamicTaskEnabled={execution?.dynamicTaskEnabled ?? true}
              workflowEnabled={enableWorkflow && WORKFLOW_TASK_ENABLED}
              disabled={disabled}
              workflows={execution?.workflows ?? []}
              workflowsLoading={execution?.workflowsLoading ?? false}
              onWorkflowHover={execution ? () => void execution.loadWorkflows() : undefined}
              showTaskEntries={showTaskEntries}
            />
            {disabled && disabledReason && <p className="mt-1 px-2 text-xs text-destructive">{disabledReason}</p>}
          </PopoverContent>
        </Popover>
        {/* 「通用协作能力」文案区：仅单聊 我的/团队 Bot 装配（capabilitySets 非空）。
         * 好友 Bot 会话与协作群不传 → 文案区与竖分隔线均不渲染，＋ 区独立保留。 */}
        {capabilitySets ? (
          <>
            <span aria-hidden="true" className="mx-2.5 h-4 w-px bg-border" />
            <Popover
              open={capabilityOpen}
              onOpenChange={(next) => {
                setCapabilityOpen(next);
                // 每次打开重拉（spec「数据刷新」）；Popover 卸载内容即重置明细态。
                if (next) capabilitySets.load();
              }}
            >
              <PopoverTrigger asChild>
                <Button
                  variant="ghost"
                  aria-label="通用协作能力"
                  aria-haspopup="menu"
                  aria-expanded={capabilityOpen}
                  className="h-7 gap-0 px-0"
                >
                  <span className="text-[13px] font-normal leading-none text-foreground">通用协作能力</span>
                  <ChevronDown aria-hidden="true" className="ml-1.5 size-3 text-muted-foreground" />
                </Button>
              </PopoverTrigger>
              {/* 能力集气泡（PRD demo TokenComposer 规格）：320px 双页卡片，自带头部/滚动，
               * 阴影沿用 demo 大阴影 0_16px_40px fg/0.12。 */}
              <PopoverContent
                align="start"
                className="capability-popover w-[320px] rounded-2xl p-0 shadow-[0_16px_40px_hsl(var(--foreground)/0.12)]"
              >
                <CapabilitySetViewer state={capabilitySets} />
              </PopoverContent>
            </Popover>
          </>
        ) : null}
      </div>
      {(selections ?? []).map((selection) => {
        const key = composerSelectionKey(selection);
        return (
          <SelectionChip
            key={key}
            label={selection.kind === 'workflow' ? selection.workflow.title : '任务'}
            onClear={() => onRemoveSelection?.(key)}
          />
        );
      })}
    </div>
  );
}
