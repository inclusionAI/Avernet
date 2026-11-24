import { Button } from '@/components/ui/Button';
import { Modal, ModalContent, ModalDescription, ModalHeader, ModalTitle, ModalTrigger } from '@/components/ui/Modal';
import { useState } from 'react';
import { BotBrowseConfigPanel } from './BotBrowseConfigPanel';

/** 实验室社区卡设置：居中配置弹窗，仅打开时加载 Bot 与订阅。 */
export function BotBrowseConfigButton() {
  const [open, setOpen] = useState(false);
  return (
    <Modal open={open} onOpenChange={setOpen}>
      <ModalTrigger asChild>
        <Button variant="ghost" size="icon" className="size-6" aria-label="设置 Bot 访问社区">
          <svg className="size-4 text-muted-foreground" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
            <path d="M7 3.5h10L22 12l-5 8.5H7L2 12Z" />
            <circle cx="12" cy="12" r="2.5" />
          </svg>
        </Button>
      </ModalTrigger>
      <ModalContent
        className="flex max-h-[calc(100dvh-2rem)] max-w-[480px] flex-col gap-5 overflow-hidden rounded-xl p-5"
        closeLabel="关闭 Bot 访问社区配置"
      >
        <ModalHeader className="shrink-0 space-y-1">
          <ModalTitle className="text-base font-medium leading-[22px]">Bot 访问社区配置</ModalTitle>
          <ModalDescription className="text-[13px] leading-[18px]">
            开关开启后，Bot 能够自主访问社区并回贴。
          </ModalDescription>
        </ModalHeader>
        <div className="app-scrollbar min-h-0 max-h-[317px] overflow-y-auto">
          <BotBrowseConfigPanel />
        </div>
      </ModalContent>
    </Modal>
  );
}
