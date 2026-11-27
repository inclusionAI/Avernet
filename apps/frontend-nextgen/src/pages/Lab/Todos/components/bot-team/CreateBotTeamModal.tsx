import { Button, Input, Textarea } from '@/components/ui';
import { Modal, ModalContent, ModalDescription, ModalFooter, ModalHeader, ModalTitle } from '@/components/ui/Modal';
import { Workflow } from 'lucide-react';
import { useEffect, useState } from 'react';

interface CreateBotTeamModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCreate: (input: { name: string; description: string }) => void;
}

export function CreateBotTeamModal({ open, onOpenChange, onCreate }: CreateBotTeamModalProps) {
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [submitted, setSubmitted] = useState(false);

  useEffect(() => {
    if (!open) return;
    setName('');
    setDescription('');
    setSubmitted(false);
  }, [open]);

  const create = () => {
    setSubmitted(true);
    if (!name.trim()) return;
    onCreate({ name: name.trim(), description: description.trim() });
  };

  return (
    <Modal open={open} onOpenChange={onOpenChange}>
      <ModalContent size="md" aria-describedby={undefined}>
        <ModalHeader>
          <ModalTitle className="text-base">创建小队</ModalTitle>
          <ModalDescription>先填写基本信息，创建后继续配置线性工作流。</ModalDescription>
        </ModalHeader>
        <div className="space-y-4">
          <label className="block space-y-1.5 text-sm font-medium text-foreground">
            <span>名称</span>
            <Input value={name} onChange={(event) => setName(event.target.value)} placeholder="如：数据分析小队" />
            {submitted && !name.trim() ? <span className="block text-xs text-destructive">请输入小队名称</span> : null}
          </label>
          <label className="block space-y-1.5 text-sm font-medium text-foreground">
            <span>描述</span>
            <Textarea
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              placeholder="小队擅长场景与节点能力概要"
              rows={3}
            />
          </label>
          <fieldset className="space-y-1.5">
            <legend className="text-sm font-medium text-foreground">类型</legend>
            <div className="grid grid-cols-2 gap-2">
              <div className="flex flex-col items-start gap-1 rounded-lg border border-primary bg-primary/5 p-3 text-left">
                <Workflow className="size-5 text-primary" aria-hidden />
                <span className="text-sm font-medium text-foreground">工作流</span>
              </div>
            </div>
          </fieldset>
        </div>
        <ModalFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button onClick={create}>创建</Button>
        </ModalFooter>
      </ModalContent>
    </Modal>
  );
}
