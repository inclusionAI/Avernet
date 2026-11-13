// 任务无关附件菜单（collab 形态群聊输入区 ＋ 入口）：GroupChatComposer 在群任务槽位为 null
// 或未装配任务执行时以本组件承接「上传文件/添加图片」。仅依赖 ui 原语（Button/Popover），
// 严禁 import 任务栈模块（TaskComposerMenu 及其 barrel）——否则会经 GroupChatPane 静态图把
// 任务栈拖进 collab 产物，破坏 slots.ts 的模块图不变量。
// 视觉口径沿用 ComposerCapabilitiesMenu 实测规格（dmore：28×28 圆钮 / 菜单 162 宽 / 行 34px）。
import { Button, Popover, PopoverContent, PopoverTrigger } from '@/components/ui';
import { FileUp, ImageDown, Plus } from 'lucide-react';
import { useState } from 'react';

interface AttachmentMenuProps {
  onUpload: () => void;
  onAddImage: () => void;
  disabled: boolean;
}

/** 菜单行（与 TaskComposerMenu 菜单行同视觉：34px 单行，icon 14px + 13px 文案）。 */
function AttachmentMenuRow({ icon, label, onClick }: { icon: React.ReactNode; label: string; onClick: () => void }) {
  return (
    <Button
      variant="ghost"
      onClick={onClick}
      className="h-[34px] w-full justify-start gap-1 rounded-md px-2.5 text-left text-[13px] font-normal text-foreground"
    >
      <span aria-hidden="true" className="flex size-3.5 shrink-0 items-center justify-center">
        {icon}
      </span>
      <span className="min-w-0 flex-1 truncate">{label}</span>
    </Button>
  );
}

export function AttachmentMenu({ onUpload, onAddImage, disabled }: AttachmentMenuProps) {
  const [open, setOpen] = useState(false);
  // 选择后关菜单再执行入口（与能力菜单选择行为一致，避免气泡残留遮挡后续界面）。
  const pick = (fn: () => void) => () => {
    setOpen(false);
    fn();
  };

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          variant="ghost"
          aria-label="添加附件"
          aria-haspopup="menu"
          aria-expanded={open}
          disabled={disabled}
          className="size-7 rounded-full p-0"
        >
          <Plus className="size-3" />
        </Button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-[162px] rounded-xl p-[7px]">
        <div className="flex flex-col">
          <AttachmentMenuRow icon={<FileUp className="size-3.5" />} label="上传文件" onClick={pick(onUpload)} />
          <AttachmentMenuRow icon={<ImageDown className="size-3.5" />} label="添加图片" onClick={pick(onAddImage)} />
        </div>
      </PopoverContent>
    </Popover>
  );
}
