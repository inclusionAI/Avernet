import { Button } from '@/components/ui/Button';
import { Modal, ModalContent, ModalDescription, ModalFooter, ModalHeader, ModalTitle } from '@/components/ui/Modal';
import { Textarea } from '@/components/ui/Textarea';
import { useFeedback } from '@/hooks/useFeedback';
import { useEffect, useState } from 'react';

interface FeedbackModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/** 实验室「我要反馈」弹窗：输入反馈内容后以当前登录人提交（module='bbs'，openapi §5.2.1）。 */
export function FeedbackModal({ open, onOpenChange }: FeedbackModalProps) {
  const { submit, submitting } = useFeedback();
  const [content, setContent] = useState('');
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) {
      setContent('');
      setError(null);
    }
  }, [open]);

  const handleSubmit = async () => {
    const trimmed = content.trim();
    if (!trimmed) {
      setError('请输入反馈内容');
      return;
    }
    if (trimmed.length > 4000) {
      setError('反馈内容不能超过 4000 字');
      return;
    }
    if (await submit(trimmed)) onOpenChange(false);
  };

  return (
    <Modal open={open} onOpenChange={(next) => !submitting && onOpenChange(next)}>
      <ModalContent size="lg">
        <ModalHeader>
          <ModalTitle>我要反馈</ModalTitle>
          <ModalDescription>反馈关于实验性功能（社区等）的体验与建议，我们会持续优化。</ModalDescription>
        </ModalHeader>
        <div className="space-y-2 py-1">
          <label className="block space-y-1.5 text-sm font-medium text-foreground" htmlFor="feedback-content">
            <span>反馈内容</span>
            <Textarea
              id="feedback-content"
              size="lg"
              className="min-h-40"
              value={content}
              onChange={(event) => {
                setContent(event.target.value);
                if (error) setError(null);
              }}
              placeholder="描述你遇到的问题或建议，最多 4000 字"
              maxLength={4000}
              variant={error ? 'error' : 'default'}
              aria-invalid={!!error}
            />
            {error && <span className="text-xs text-destructive">{error}</span>}
          </label>
        </div>
        <ModalFooter>
          <Button variant="outline" disabled={submitting} onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button loading={submitting} onClick={() => void handleSubmit()}>
            提交反馈
          </Button>
        </ModalFooter>
      </ModalContent>
    </Modal>
  );
}
