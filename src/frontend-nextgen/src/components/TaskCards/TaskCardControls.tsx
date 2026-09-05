import { Button } from '@/components/ui';
import { cn } from '@/utils/cn';
import { Check, X } from 'lucide-react';

/**
 * TaskReadyCard 复用的纯展示控件 + 按钮观感常量（按原始 card SDK 规格自绘）。
 * 守卫要求交互用 <Button>；此处集中维护 className 全量覆盖，避免主组件膨胀超 300 行。
 */

/** 编辑入口（铅笔）按钮：禁用态保持不淡化，与原卡片「灰色文案」一致。 */
export const taskCardEditBtnClass = (isDisabled: boolean) =>
  cn(
    'inline-flex items-center gap-0.5 px-1 py-0.5 rounded-md text-[9px] transition-all duration-300 leading-none h-auto w-auto border-transparent font-normal disabled:opacity-100',
    isDisabled ? 'text-gray-300 cursor-not-allowed' : 'text-blue-500 hover:bg-blue-50 hover:scale-105 active:scale-95',
  );

export const taskCardConfirmBtnClass =
  'inline-flex items-center justify-center gap-0.5 px-2.5 py-1 rounded-md bg-blue-500 text-white text-[9px] font-medium hover:bg-blue-600 hover:scale-105 active:scale-95 transition-all duration-300 leading-none h-auto border-transparent';

export const taskCardCancelBtnClass =
  'inline-flex items-center justify-center gap-0.5 px-2.5 py-1 rounded-md bg-muted text-gray-500 text-[9px] font-medium hover:bg-muted/80 hover:scale-105 active:scale-95 transition-all duration-300 leading-none h-auto border-transparent';

export const taskCardTextareaClass =
  'min-h-0 w-full p-1.5 rounded-lg border-blue-200 px-1.5 text-[11px] text-gray-700 resize-none focus-visible:border-blue-300 focus-visible:ring-2 focus-visible:ring-blue-300/50';

export const taskCardDiscardBtnClass =
  'flex-1 inline-flex items-center justify-center gap-0.5 h-auto px-1.5 py-1 rounded-lg border border-red-200 text-red-500 text-[11px] font-medium hover:bg-red-50 hover:scale-105 active:scale-95 transition-all duration-300 leading-none';

export const taskCardSaveBtnClass =
  'flex-1 inline-flex items-center justify-center gap-0.5 h-auto px-1.5 py-1 rounded-lg border border-gray-200 text-gray-500 text-[11px] font-medium hover:bg-muted/50 hover:scale-105 active:scale-95 transition-all duration-300 leading-none';

export const taskCardExecuteBtnClass =
  'flex-1 inline-flex items-center justify-center gap-0.5 h-auto px-1.5 py-1 rounded-lg bg-blue-500 text-white text-[11px] font-medium hover:bg-blue-600 hover:scale-105 active:scale-95 transition-all duration-300 shadow-sm leading-none border-transparent';

/** 编辑态「确认 / 取消」二按组合。 */
export function EditFieldControls({ onConfirm, onCancel }: { onConfirm: () => void; onCancel: () => void }) {
  return (
    <div className="flex items-center gap-1">
      <Button variant="ghost" className={taskCardConfirmBtnClass} onClick={onConfirm}>
        <Check className="h-2 w-2" aria-hidden />
        确认
      </Button>
      <Button variant="ghost" className={taskCardCancelBtnClass} onClick={onCancel}>
        <X className="h-2 w-2" aria-hidden />
        取消
      </Button>
    </div>
  );
}
