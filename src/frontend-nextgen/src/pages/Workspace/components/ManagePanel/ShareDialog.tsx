import { Badge, Button, Input, Modal, ModalContent, ModalHeader, ModalTitle } from '@/components/ui';
import { Check, Copy } from 'lucide-react';
import { useState } from 'react';
import { toast } from 'sonner';

export interface ShareDialogProps {
  open: boolean;
  title: string;
  inviting: boolean;
  invitationUrl: string | null;
  onClose: () => void;
}

/** 群/会话邀请链接弹窗：生成成功后展示链接并支持一键复制。 */
export function ShareDialog({ open, title, inviting, invitationUrl, onClose }: ShareDialogProps) {
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    if (!invitationUrl) return;
    try {
      await navigator.clipboard.writeText(invitationUrl);
      setCopied(true);
      toast.success('邀请链接已复制');
    } catch {
      toast.error('复制失败，请手动复制。');
    }
  };

  return (
    <Modal open={open} onOpenChange={(next) => !inviting && !next && onClose()}>
      <ModalContent size="md" closeLabel="关闭分享弹窗">
        <ModalHeader>
          <ModalTitle className="m-0 flex items-center gap-2 text-base font-semibold text-foreground">
            分享{title}
            {inviting && <Badge tone="warning">生成中</Badge>}
          </ModalTitle>
        </ModalHeader>
        <div className="space-y-3">
          <p className="m-0 text-sm text-muted-foreground">分享链接生成后，将允许对应成员通过链接加入{title}。</p>
          <Input readOnly value={invitationUrl ?? ''} placeholder="点击下方按钮生成邀请链接" aria-label="邀请链接" />
          <div className="flex justify-end">
            <Button
              variant="secondary"
              loading={inviting}
              disabled={!invitationUrl}
              leftIcon={copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
              onClick={() => void handleCopy()}
            >
              复制链接
            </Button>
          </div>
        </div>
      </ModalContent>
    </Modal>
  );
}
