import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Modal, ModalContent, ModalDescription, ModalFooter, ModalHeader, ModalTitle } from '@/components/ui/Modal';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/Select';
import { Textarea } from '@/components/ui/Textarea';
import type { TodoBotOption } from '@/domain/todos/models';
import { useEffect, useState } from 'react';

interface TodoFormValues {
  title: string;
  description: string;
  botId: string;
}

interface TodoCreateModalProps {
  open: boolean;
  bots: TodoBotOption[];
  loading: boolean;
  onOpenChange: (open: boolean) => void;
  onSubmit: (input: TodoFormValues) => Promise<boolean>;
}

export function TodoCreateModal({ open, bots, loading, onOpenChange, onSubmit }: TodoCreateModalProps) {
  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
  const [botId, setBotId] = useState('');
  useEffect(() => {
    if (!open) return;
    setTitle('');
    setDescription('');
    setBotId(bots[0]?.botId || '');
  }, [bots, open]);

  const submit = async () => {
    if (!title.trim() || !botId) return;
    const success = await onSubmit({ title: title.trim(), description: description.trim(), botId });
    if (success) onOpenChange(false);
  };

  return (
    <Modal open={open} onOpenChange={onOpenChange}>
      <ModalContent size="lg" aria-describedby={undefined}>
        <ModalHeader>
          <ModalTitle>创建待办</ModalTitle>
          <ModalDescription>待办创建后进入待处理状态，系统将自动调度所选 Bot 执行。</ModalDescription>
        </ModalHeader>
        <div className="space-y-4">
          <label className="block space-y-1.5 text-xs font-medium text-foreground">
            <span>标题</span>
            <Input
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              placeholder="例如：分析本周用户反馈"
              autoFocus
            />
          </label>
          <label className="block space-y-1.5 text-xs font-medium text-foreground">
            <span>描述</span>
            <Textarea
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              placeholder="补充 Bot 需要完成的目标和要求"
              rows={5}
            />
          </label>
          <label className="block space-y-1.5 text-xs font-medium text-foreground">
            <span>选择 Bot</span>
            <Select value={botId || undefined} onValueChange={setBotId} disabled={bots.length === 0}>
              <SelectTrigger aria-label="选择 Bot">
                <SelectValue placeholder={bots.length ? '请选择 Bot' : '当前空间暂无可用 Bot'} />
              </SelectTrigger>
              <SelectContent>
                {bots.map((bot) => (
                  <SelectItem key={bot.botId} value={bot.botId}>
                    {bot.botName || bot.botId}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </label>
          {bots.length === 0 ? (
            <p className="m-0 text-xs text-muted-foreground">当前空间没有可用 Bot，暂时无法创建待办。</p>
          ) : null}
        </div>
        <ModalFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button onClick={() => void submit()} loading={loading} disabled={!title.trim() || !botId}>
            创建待办
          </Button>
        </ModalFooter>
      </ModalContent>
    </Modal>
  );
}
