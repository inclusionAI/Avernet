// 创建团队空间表单:Modal 内 Input(space_name,必填非空) + 提交/取消。字段结构与 SpaceJoinForm 对齐。
import {
  Button,
  CaptionText,
  Input,
  Modal,
  ModalContent,
  ModalFooter,
  ModalHeader,
  ModalTitle,
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui';
import { CircleHelp } from 'lucide-react';
import { useState } from 'react';

export interface SpaceCreateFormProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSubmit: (spaceName: string) => Promise<boolean>;
}

export function SpaceCreateForm({ open, onOpenChange, onSubmit }: SpaceCreateFormProps) {
  const [name, setName] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const submit = async () => {
    const trimmed = name.trim();
    if (!trimmed || submitting) return;
    setSubmitting(true);
    try {
      if (await onSubmit(trimmed)) {
        setName('');
        onOpenChange(false);
      }
    } finally {
      setSubmitting(false);
    }
  };

  const changeOpen = (next: boolean) => {
    if (submitting) return;
    if (!next) setName('');
    onOpenChange(next);
  };

  return (
    <Modal open={open} onOpenChange={changeOpen}>
      <ModalContent size="md" className="max-w-[480px] gap-0 p-0" aria-describedby={undefined}>
        <ModalHeader className="h-[57px] justify-center border-b border-border px-5">
          <div className="flex items-center gap-1">
            <ModalTitle className="text-base font-medium leading-6">创建新团队空间</ModalTitle>
            <TooltipProvider delayDuration={200}>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button variant="ghost" size="icon" className="size-4 rounded-full p-0" aria-label="创建团队空间说明">
                    <CircleHelp size={16} aria-hidden />
                  </Button>
                </TooltipTrigger>
                <TooltipContent side="bottom" className="whitespace-pre-line text-[13px] leading-5">
                  {'个人空间为系统自动分配，无需创建。\n此处仅可创造团队空间。'}
                </TooltipContent>
              </Tooltip>
            </TooltipProvider>
          </div>
        </ModalHeader>
        <div className="px-5 py-5">
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <CaptionText as="label" htmlFor="space-create-name" className="text-[14px] text-foreground">
                <span className="mr-1 text-destructive">*</span>空间名称
              </CaptionText>
            </div>
            <Input
              id="space-create-name"
              className="h-8 text-[13px]"
              placeholder="请输入空间名称"
              value={name}
              disabled={submitting}
              onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') void submit();
              }}
              autoFocus
            />
          </div>
        </div>
        <ModalFooter className="flex-row justify-end border-t border-border px-5 py-4">
          <Button
            variant="secondary"
            size="sm"
            className="h-8 px-4 text-[13px]"
            onClick={() => changeOpen(false)}
            disabled={submitting}
          >
            取消
          </Button>
          <Button
            variant="primary"
            size="sm"
            className="h-8 px-4 text-[13px]"
            onClick={() => void submit()}
            disabled={submitting || !name.trim()}
          >
            {submitting ? '创建中…' : '确定'}
          </Button>
        </ModalFooter>
      </ModalContent>
    </Modal>
  );
}
