// 创建团队空间表单:Modal 内 Input(space_name,必填非空) + 提交/取消。字段结构与 SpaceJoinForm 对齐。
import {
  Button,
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
      <ModalContent size="sm" className="max-w-[420px]">
        <ModalHeader>
          <ModalTitle>创建团队空间</ModalTitle>
          <ModalDescription>创建一个独立的团队工作空间,用于多人协作。</ModalDescription>
        </ModalHeader>
        {/* 字段块结构与 SpaceJoinForm 对齐:外层 space-y-4 留多字段间距,内层 space-y-2 让 label 贴近输入框 */}
        <div className="space-y-4 py-2">
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <label htmlFor="space-create-name" className="text-xs text-muted-foreground">
                空间名称（必填）
              </label>
            </div>
            <Input
              id="space-create-name"
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
          <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={submitting}>
            取消
          </Button>
          <Button variant="primary" onClick={() => void submit()} disabled={submitting || !name.trim()}>
            {submitting ? '创建中…' : '创建'}
          </Button>
        </ModalFooter>
      </ModalContent>
    </Modal>
  );
}
