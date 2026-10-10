import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Modal, ModalContent, ModalFooter, ModalHeader, ModalTitle } from '@/components/ui/Modal';
import { Textarea } from '@/components/ui/Textarea';
import { useEffect, useState } from 'react';

interface PublishTopicModalProps {
  open: boolean;
  publishing: boolean;
  onOpenChange: (open: boolean) => void;
  onPublish: (title: string, body: string) => Promise<boolean>;
}

export function PublishTopicModal({ open, publishing, onOpenChange, onPublish }: PublishTopicModalProps) {
  const [title, setTitle] = useState('');
  const [body, setBody] = useState('');
  const [errors, setErrors] = useState<{ title?: string; body?: string }>({});

  useEffect(() => {
    if (!open) {
      setTitle('');
      setBody('');
      setErrors({});
    }
  }, [open]);

  const handleSubmit = async () => {
    const nextErrors = {
      ...(!title.trim() ? { title: '请输入主题标题' } : {}),
      ...(!body.trim() ? { body: '请输入主题正文' } : {}),
    };
    setErrors(nextErrors);
    if (Object.keys(nextErrors).length) return;
    if (await onPublish(title, body)) onOpenChange(false);
  };

  return (
    <Modal open={open} onOpenChange={(next) => !publishing && onOpenChange(next)}>
      <ModalContent size="lg">
        <ModalHeader>
          <ModalTitle>发布主题</ModalTitle>
        </ModalHeader>
        <div className="space-y-4">
          <label className="block space-y-1.5 text-sm font-medium text-foreground">
            <span>主题标题</span>
            <Input
              value={title}
              onChange={(event) => {
                setTitle(event.target.value);
                setErrors((value) => ({ ...value, title: undefined }));
              }}
              placeholder="用一句话概括你想讨论的内容"
              maxLength={80}
              aria-invalid={!!errors.title}
            />
            {errors.title && <span className="text-xs text-destructive">{errors.title}</span>}
          </label>
          <label className="block space-y-1.5 text-sm font-medium text-foreground">
            <span>正文</span>
            <Textarea
              className="min-h-40"
              value={body}
              onChange={(event) => {
                setBody(event.target.value);
                setErrors((value) => ({ ...value, body: undefined }));
              }}
              placeholder="补充背景、问题和你希望获得的反馈"
              maxLength={4000}
              variant={errors.body ? 'error' : 'default'}
              aria-invalid={!!errors.body}
            />
            {errors.body && <span className="text-xs text-destructive">{errors.body}</span>}
          </label>
        </div>
        <ModalFooter>
          <Button variant="outline" disabled={publishing} onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button loading={publishing} onClick={() => void handleSubmit()}>
            发布
          </Button>
        </ModalFooter>
      </ModalContent>
    </Modal>
  );
}
