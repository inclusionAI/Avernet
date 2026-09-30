// 创建团队空间表单:Modal 内 Input(space_name,必填非空) + 提交/取消。字段结构与 SpaceJoinForm 对齐。
import {
  Button,
  CaptionText,
  Input,
  Modal,
  ModalContent,
  ModalDescription,
  ModalFooter,
  ModalHeader,
  ModalTitle,
} from '@/components/ui';
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
    if (!trimmed) return;
    setSubmitting(true);
    const ok = await onSubmit(trimmed);
    setSubmitting(false);
    if (ok) {
      setName('');
      onOpenChange(false);
    }
  };

  return (
    <Modal open={open} onOpenChange={onOpenChange}>
      {/* 底部已有取消按钮，关闭入口不重复，隐藏右上角叉叉 */}
      <ModalContent size="sm" className="max-w-[420px]" showClose={false}>
        {/* 标题与说明间距覆盖为 4px（ModalHeader 基座 6px 非 4px 倍数） */}
        <ModalHeader className="space-y-1">
          <ModalTitle className="text-[14px] font-semibold leading-5">创建团队空间</ModalTitle>
          <ModalDescription className="text-[12px] leading-4">
            创建一个独立的团队工作空间,用于多人协作。
          </ModalDescription>
        </ModalHeader>
        {/* 字段块结构与 SpaceJoinForm 对齐:外层 space-y-4 留多字段间距,内层 space-y-2 让 label 贴近输入框 */}
        <div className="space-y-4 py-2">
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              {/* label 14px + 正文字色为受控偏离（标准 12px + muted），经用户确认，见 docs/design-migration/admin-space-management-migration.md */}
              <CaptionText as="label" htmlFor="space-create-name" className="text-[14px] text-foreground">
                空间名称（必填）
              </CaptionText>
            </div>
            <Input
              id="space-create-name"
              className="text-[12px]"
              placeholder="请输入空间名称"
              value={name}
              onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') void submit();
              }}
              autoFocus
            />
          </div>
        </div>
        <ModalFooter>
          {/* 按钮水平内边距 18px = sm 基座 12px 的 1.5 倍，用户指定（非 4px 倍数，受控偏离） */}
          {/* 取消按钮样式对齐空间卡片「申请加入」按钮：secondary 线框 + 12px semibold */}
          <Button
            variant="secondary"
            size="sm"
            className="px-[18px] text-[12px] font-semibold"
            onClick={() => onOpenChange(false)}
            disabled={submitting}
          >
            取消
          </Button>
          <Button
            variant="primary"
            size="sm"
            className="px-[18px] text-[12px]"
            onClick={() => void submit()}
            disabled={submitting || !name.trim()}
          >
            {submitting ? '创建中…' : '创建'}
          </Button>
        </ModalFooter>
      </ModalContent>
    </Modal>
  );
}
