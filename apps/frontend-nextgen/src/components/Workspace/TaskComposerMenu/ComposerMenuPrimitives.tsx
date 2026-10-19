// ComposerCapabilitiesMenu 的菜单原语(从主组件拆出,文件大小阈值):
// MenuRow(菜单行)与 SelectionChip(选中态胶囊)均为纯展示件,无自身业务。
import { Badge, Button } from '@/components/ui';
import { X } from 'lucide-react';
import React from 'react';

/** 菜单行：单行 34px（dmore 148×34 rx6，icon 14px + 13px 文案，无 desc）。 */
export const MenuRow = React.forwardRef<
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

/** 选中态 chip：并列叠加形态（多胶囊语义），x 单独清除；形状沿用 dmore 选中帧。 */
export function SelectionChip({ label, onClear }: { label: string; onClear: () => void }) {
  return (
    <Badge tone="primary" className="gap-1 py-0.5 pr-1">
      <span className="max-w-[160px] truncate">{label}</span>
      <Button variant="ghost" size="icon" onClick={onClear} className="h-4 w-4" aria-label="取消选择">
        <X className="h-3 w-3" />
      </Button>
    </Badge>
  );
}