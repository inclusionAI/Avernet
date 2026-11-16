/** CapabilitySetEntryPill —— 「＋ | 通用协作能力」双区胶囊的能力集文案区（自
 * ComposerCapabilitiesMenu 拆出：TC-G005 行数门禁）。显示首个真实激活集名 +
 * 多集个数徽标，点击打开只读查看气泡（CapabilitySetViewer，每次打开重拉）。
 * 未加载/空集回落默认「通用协作能力」；长名截断的悬停全显走 Tooltip 组件
 * （TC-G006：title 属性不能替代 Tooltip）。 */
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
import type { ComposerCapabilitySetsState } from '@/components/Workspace/CapabilitySetViewer/types';
import { ChevronDown } from 'lucide-react';
import { useState } from 'react';

export function CapabilitySetEntryPill({ state }: { state: ComposerCapabilitySetsState }) {
  const [open, setOpen] = useState(false);
  const activeSets = state.sets ?? [];
  const setName = activeSets.length ? activeSets[0].name : '通用协作能力';
  const entryLabel = activeSets.length > 1 ? `${setName} 等 ${activeSets.length} 个能力集` : setName;
  const fullName = activeSets.map((set) => set.name).join('、');
  return (
    <>
      <span aria-hidden="true" className="ml-2.5 h-4 w-px bg-divider" />
      <Popover
        open={open}
        onOpenChange={(next) => {
          setOpen(next);
          // 每次打开重拉（spec「数据刷新」）；Popover 卸载内容即重置明细态。
          if (next) state.load();
        }}
      >
        <PopoverTrigger asChild>
          <Button
            variant="ghost"
            aria-label={entryLabel}
            aria-haspopup="menu"
            aria-expanded={open}
            className="h-7 gap-0 px-2"
          >
            {/* 真实名称可能较长：截断保护 + Tooltip 悬停全显（多集名称列表）。 */}
            <TooltipProvider>
              <Tooltip>
                <TooltipTrigger asChild>
                  <span className="max-w-40 truncate text-[13px] font-normal leading-none text-foreground">
                    {setName}
                  </span>
                </TooltipTrigger>
                <TooltipContent>{fullName}</TooltipContent>
              </Tooltip>
            </TooltipProvider>
            {activeSets.length > 1 && (
              <span
                aria-hidden="true"
                className="ml-1 rounded bg-muted px-1 text-[10px] font-normal leading-4 text-muted-foreground"
              >
                {activeSets.length}
              </span>
            )}
            <ChevronDown aria-hidden="true" className="ml-1.5 size-3 text-muted-foreground" />
          </Button>
        </PopoverTrigger>
        {/* 能力集气泡（PRD demo TokenComposer 规格）：320px 双页卡片，自带头部/滚动，
         * 阴影沿用 demo 大阴影 0_16px_40px fg/0.12。 */}
        <PopoverContent
          align="start"
          className="capability-popover w-[320px] rounded-2xl p-0 shadow-[0_16px_40px_hsl(var(--foreground)/0.12)]"
        >
          <CapabilitySetViewer state={state} />
        </PopoverContent>
      </Popover>
    </>
  );
}
