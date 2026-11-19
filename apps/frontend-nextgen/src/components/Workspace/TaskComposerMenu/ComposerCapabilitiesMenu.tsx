// @asset-migrated: teamclaw 自研
/** ComposerCapabilitiesMenu —— Sender 左侧「＋ | 通用协作能力」双区胶囊。
 *
 * 胶囊拆区（add-composer-capability-set-viewer，2026-10-14）：DMore 179×28 胶囊保持原视觉，
 * 以竖分隔线为界拆为两个独立触发区——
 * - ＋ 圆钮（aria「添加附件」）：打开原混排菜单（上传文件/添加图片/文件管理 + 任务/工作流任务），行为不变；
 * - 「通用协作能力 ⌄」文案区：仅当调用方装配 capabilitySets（单聊 我的/团队空间 Bot）时渲染，
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
import { Button, Popover, PopoverContent, PopoverTrigger } from '@/components/ui';
import { CapabilitySetEntryPill } from '@/components/Workspace/TaskComposerMenu/CapabilitySetEntryPill';
import { SelectionChip } from '@/components/Workspace/TaskComposerMenu/ComposerMenuPrimitives';
import { composerSelectionKey } from '@/services/tasks/taskMapper';
import { cn } from '@/utils/cn';
import { Plus } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { ComposerCapabilitiesMenuBody } from './ComposerCapabilitiesMenuBody';
import type { WorkflowPanelItem } from './WorkflowSubPanel';

/** 功能开关。clawweb 内部环境 /api/workflows 已实测可用（restore-design-chat-page-b2-b3 design.md D4 回填），开闸。 */
const WORKFLOW_TASK_ENABLED = true;

import type { ComposerCapabilitiesMenuProps } from './composerCapabilitiesMenuTypes';

export type { ComposerCapabilitiesMenuProps } from './composerCapabilitiesMenuTypes';

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
  onAgentCompute,
  onEvolution,
  onRemoveSelection,
  capabilitySets,
  showTaskEntries = true,
  extraMenuRows,
  className,
  onOpened,
}: ComposerCapabilitiesMenuProps) {
  const [open, setOpen] = useState(false);
  const close = () => setOpen(false);
  // 已激活能力集入口（2026-11-04 需求）：挂载预载一次，入口初见即显示真实激活集名
  // 而非默认占位（load 自带 in-flight 去重；气泡打开时的重拉由 pill 的 onOpenChange 承载）。
  const capabilitySetsRef = useRef(capabilitySets);
  useEffect(() => {
    capabilitySetsRef.current?.load();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- 仅挂载预载，引用变化不重复拉取。
  }, []);
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
            <ComposerCapabilitiesMenuBody
              onUpload={withClose(onUpload)}
              onAddImage={withClose(onAddImage)}
              onManageFiles={withClose(onManageFiles)}
              onDynamic={
                execution
                  ? () => {
                      onDynamicSelected?.();
                      close();
                    }
                  : undefined
              }
              onPickWorkflow={execution && enableWorkflow && WORKFLOW_TASK_ENABLED ? pickWorkflow : undefined}
              onAgentCompute={onAgentCompute ? withClose(onAgentCompute) : undefined}
              onEvolution={withClose(onEvolution)}
              dynamicTaskEnabled={execution?.dynamicTaskEnabled ?? true}
              workflowEnabled={enableWorkflow && WORKFLOW_TASK_ENABLED}
              disabled={disabled}
              workflows={execution?.workflows ?? []}
              workflowsLoading={execution?.workflowsLoading ?? false}
              onWorkflowHover={execution ? () => void execution.loadWorkflows() : undefined}
              showTaskEntries={showTaskEntries}
              extraMenuRows={extraMenuRows?.(close)}
            />
            {disabled && disabledReason && <p className="mt-1 px-2 text-xs text-destructive">{disabledReason}</p>}
          </PopoverContent>
        </Popover>
        {/* 「通用协作能力」文案区：仅单聊 我的/团队空间 Bot 装配（capabilitySets 非空）。
         * 好友 Bot 会话与协作群不传 → 文案区与竖分隔线均不渲染，＋ 区独立保留。
         * 入口 pill（真实集名/徽标/查看气泡/Tooltip）拆至 CapabilitySetEntryPill（TC-G005）。 */}
        {capabilitySets ? <CapabilitySetEntryPill state={capabilitySets} /> : null}
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
