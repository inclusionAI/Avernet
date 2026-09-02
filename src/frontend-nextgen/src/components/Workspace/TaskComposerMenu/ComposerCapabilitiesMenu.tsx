// @asset-migrated: teamclaw 自研
/** ComposerCapabilitiesMenu —— Sender 左侧「+」能力菜单 + 选中态 chip。 */
import { Badge, Button, Input, Popover, PopoverContent, PopoverTrigger } from '@/components/ui';
import type { UseTaskExecutionResult, WorkflowSelection } from '@/hooks/useTaskExecution';
import { cn } from '@/utils/cn';
import { FileUp, FolderOpen, ImageDown, Plus, Search, Sparkles, Workflow, X } from 'lucide-react';
import React, { useEffect, useRef, useState } from 'react';

/** 工作流任务功能暂未开放：前端屏蔽点击（始终展示该项、不可点）；开放时 flip 为 true 恢复能力。 */
const WORKFLOW_TASK_ENABLED = false;

export interface ComposerCapabilitiesMenuProps {
  execution: UseTaskExecutionResult;
  onUpload?: () => void;
  /** 添加图片回调（打开文件选择器）。提供则显示「添加图片」项。 */
  onAddImage?: () => void;
  /** 文件管理回调（打开文件管理 Modal）。提供则显示「文件管理」项（上传文件后、动态任务前）。 */
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

const MenuRow = React.forwardRef<
  HTMLButtonElement,
  {
    icon: React.ReactNode;
    label: string;
    desc: string;
    onClick?: () => void;
    disabled?: boolean;
    onMouseEnter?: () => void;
    onMouseLeave?: () => void;
  }
>(({ icon, label, desc, onClick, disabled, onMouseEnter, onMouseLeave }, ref) => (
  <Button
    ref={ref}
    variant="ghost"
    onClick={onClick}
    disabled={disabled}
    onMouseEnter={onMouseEnter}
    onMouseLeave={onMouseLeave}
    leftIcon={<span className="text-primary">{icon}</span>}
    className="h-auto w-full justify-start px-3 py-2 text-left"
  >
    <span className="flex flex-col">
      <span className="font-medium text-foreground">{label}</span>
      <span className="text-xs text-muted-foreground">{desc}</span>
    </span>
  </Button>
));
MenuRow.displayName = 'MenuRow';

function WorkflowListView({
  workflows,
  loading,
  onPick,
}: {
  workflows: { workflowId: string; title: string }[];
  loading: boolean;
  onPick: (w: { workflowId: string; title: string }) => void;
}) {
  const [q, setQ] = useState('');
  const keyword = q.trim().toLowerCase();
  const filtered = keyword
    ? workflows.filter((w) => w.title.toLowerCase().includes(keyword) || w.workflowId.toLowerCase().includes(keyword))
    : workflows;
  return (
    <div className="flex flex-col gap-1">
      <div className="relative px-1 pb-1">
        <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
        <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="搜索工作流" className="h-7 pl-8 text-xs" />
      </div>
      {loading && <p className="px-3 py-2 text-xs text-muted-foreground">加载中…</p>}
      {!loading && filtered.length === 0 && (
        <p className="px-3 py-2 text-xs text-muted-foreground">
          {keyword ? '无匹配工作流' : '未加载到工作流，请确认 Bot 可用工作流'}
        </p>
      )}
      {filtered.map((w) => (
        <Button
          key={w.workflowId}
          variant="ghost"
          onClick={() => onPick(w)}
          className="h-auto justify-start px-3 py-2 text-left"
        >
          <span className="font-medium text-foreground text-xs">{w.title}</span>
        </Button>
      ))}
    </div>
  );
}

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

/** 菜单主体：工作流任务行 hover 触发侧边二级 Popover 展开列表。 */
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
  /** 添加图片回调（打开文件选择器）。提供则显示「添加图片」项。 */
  onAddImage?: () => void;
  onManageFiles?: () => void;
  onDynamic?: () => void;
  onPickWorkflow?: (w: { workflowId: string; title: string }) => void;
  /** 工作流任务是否可点(功能开关 × enableWorkflow)。false 时只展示、屏蔽点击，不展开二级列表。 */
  workflowEnabled: boolean;
  disabled?: boolean;
  workflows: { workflowId: string; title: string }[];
  workflowsLoading: boolean;
  onWorkflowHover?: () => void;
}) {
  const [wfOpen, setWfOpen] = useState(false);
  const closeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  // 功能未开放或整体不可用时屏蔽：行禁用且 hover 不展开二级工作流列表。
  const workflowDisabled = disabled || !workflowEnabled;
  const workflowDesc = !workflowEnabled
    ? '功能暂未开放'
    : workflowsLoading
    ? '加载工作流列表…'
    : '指定 workflow 编排执行';
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
    <div className="flex flex-col gap-1">
      <MenuRow
        icon={<FileUp className="h-4 w-4" />}
        label="上传文件"
        desc="上传文件到当前会话"
        onClick={onUpload}
        disabled={!onUpload || disabled}
      />
      <MenuRow
        icon={<ImageDown className="h-4 w-4" />}
        label="添加图片"
        desc="添加图片到当前消息"
        onClick={onAddImage}
        disabled={!onAddImage || disabled}
      />
      {onManageFiles && (
        <MenuRow
          icon={<FolderOpen className="h-4 w-4" />}
          label="文件管理"
          desc="管理本会话文件"
          onClick={onManageFiles}
          disabled={disabled}
        />
      )}
      <MenuRow
        icon={<Sparkles className="h-4 w-4" />}
        label="动态任务"
        desc="由 Owner Bot 动态规划执行"
        onClick={onDynamic}
        disabled={disabled}
      />
      <Popover open={workflowDisabled ? false : wfOpen} onOpenChange={setWfOpen}>
        <PopoverTrigger asChild>
          <MenuRow
            icon={<Workflow className="h-4 w-4" />}
            label="工作流任务"
            desc={workflowDesc}
            disabled={workflowDisabled}
            onMouseEnter={enter}
            onMouseLeave={leave}
          />
        </PopoverTrigger>
        <PopoverContent
          side="right"
          align="end"
          sideOffset={4}
          className="w-[220px] p-2"
          onMouseEnter={enter}
          onMouseLeave={leave}
        >
          <WorkflowListView
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
  const pickWorkflow = (w: { workflowId: string; title: string }) => {
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
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <Button
            variant="ghost"
            size="sm"
            disabled={disabled}
            leftIcon={<Plus className="h-3.5 w-3.5" />}
            aria-label="能力菜单"
            className="gap-1 text-xs"
          />
        </PopoverTrigger>
        <PopoverContent align="start" className="w-[260px] p-2">
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

      {hasSelection && (
        <SelectionChip
          label={selectedWorkflow ? selectedWorkflow.title : '动态任务'}
          onClear={() => onClearSelection?.()}
        />
      )}
    </div>
  );
}
