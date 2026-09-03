// 申请加入团队空间表单：Modal 内「目标空间」只读展示 + 「申请理由（选填）」textarea + 说明 + 提交/取消。
// 视觉对齐 PRD：标题「申请加入团队空间」，提交后提示审批进度查看入口。
import {
  Button,
  CaptionText,
  Modal,
  ModalContent,
  ModalFooter,
  ModalHeader,
  ModalTitle,
  Textarea,
} from '@/components/ui';
import type { Space } from '@/domain/admin/models';
import { useState } from 'react';

export interface SpaceJoinFormProps {
  space: Space;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSubmit: (reason: string) => Promise<boolean>;
}

export function SpaceJoinForm({ space, open, onOpenChange, onSubmit }: SpaceJoinFormProps) {
  const [reason, setReason] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const submit = async () => {
    setSubmitting(true);
    const ok = await onSubmit(reason.trim());
    setSubmitting(false);
    if (ok) {
      setReason('');
      onOpenChange(false);
    }
  };

  return (
    <Modal open={open} onOpenChange={onOpenChange}>
      <ModalContent size="md" className="max-w-[480px]">
        <ModalHeader>
          <ModalTitle>申请加入团队空间</ModalTitle>
        </ModalHeader>
        <div className="space-y-4 py-2">
          <div className="flex items-center gap-2">
            <span className="text-muted-foreground">目标空间：</span>
            <span className="font-medium">{space.spaceName}</span>
          </div>
          <div className="space-y-2">
            <CaptionText as="label">申请理由（选填）</CaptionText>
            <Textarea
              placeholder="希望加入该团队空间参与协作"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              rows={4}
              maxLength={512}
            />
          </div>
          <CaptionText className="m-0">
            提交后需等待该团队管理员审批，审批结果可在「工单中心 - 我发起的」中查看。
          </CaptionText>
        </div>
        <ModalFooter>
          <Button variant="ghost" size="sm" onClick={() => onOpenChange(false)} disabled={submitting}>
            取消
          </Button>
          <Button variant="primary" size="sm" onClick={() => void submit()} disabled={submitting}>
            {submitting ? '提交中…' : '提交申请'}
          </Button>
        </ModalFooter>
      </ModalContent>
    </Modal>
  );
}

export default SpaceJoinForm;
