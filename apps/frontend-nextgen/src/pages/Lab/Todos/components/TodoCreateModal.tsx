import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Modal, ModalContent, ModalDescription, ModalFooter, ModalHeader, ModalTitle } from '@/components/ui/Modal';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/Select';
import { Textarea } from '@/components/ui/Textarea';
import type { CreateTodoInput, TodoExecutor, TodoExecutorOption, TodoOwner } from '@/domain/todos/models';
import { useEffect, useState } from 'react';
import { TodoExecutorSelect } from './TodoExecutorSelect';

interface TodoCreateModalProps {
  open: boolean;
  executorOptions: TodoExecutorOption[];
  owners: TodoOwner[];
  loading: boolean;
  onOpenChange: (open: boolean) => void;
  onSubmit: (input: Omit<CreateTodoInput, 'spaceId'>) => Promise<boolean>;
}

export function TodoCreateModal({
  open,
  executorOptions,
  owners,
  loading,
  onOpenChange,
  onSubmit,
}: TodoCreateModalProps) {
  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
  const [executor, setExecutor] = useState<TodoExecutor | undefined>();
  const [ownerId, setOwnerId] = useState('');

  useEffect(() => {
    if (!open) return;
    setTitle('');
    setDescription('');
    const firstExecutor = executorOptions[0];
    setExecutor(
      firstExecutor
        ? {
            type: firstExecutor.type,
            id: firstExecutor.id,
            ...(firstExecutor.revisionId ? { revisionId: firstExecutor.revisionId } : {}),
          }
        : undefined,
    );
    setOwnerId(owners[0]?.userId ?? '');
  }, [open]);

  useEffect(() => {
    if (!open || executor || !executorOptions[0]) return;
    const first = executorOptions[0];
    setExecutor({ type: first.type, id: first.id, ...(first.revisionId ? { revisionId: first.revisionId } : {}) });
  }, [executor, executorOptions, open]);

  useEffect(() => {
    if (!open || ownerId || !owners[0]) return;
    setOwnerId(owners[0].userId);
  }, [open, ownerId, owners]);

  const submit = async () => {
    const owner = owners.find((item) => item.userId === ownerId);
    if (!title.trim() || !executor || !owner) return;
    const success = await onSubmit({
      title: title.trim(),
      description: description.trim(),
      executor,
      owner,
    });
    if (success) onOpenChange(false);
  };

  return (
    <Modal open={open} onOpenChange={onOpenChange}>
      <ModalContent size="lg" aria-describedby={undefined}>
        <ModalHeader>
          <ModalTitle>发起待办</ModalTitle>
          <ModalDescription>填写待办内容，选择负责执行的 Bot 小队，以及人工负责人。</ModalDescription>
        </ModalHeader>
        <div className="space-y-4">
          <label className="block space-y-1.5 text-sm font-medium text-foreground">
            <span>标题</span>
            <Input
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              placeholder="一句话描述这个待办"
              autoFocus
            />
          </label>
          <label className="block space-y-1.5 text-sm font-medium text-foreground">
            <span>正文</span>
            <Textarea
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              placeholder="详细描述、验收标准、上下文..."
              rows={5}
            />
          </label>
          <label className="block space-y-1.5 text-sm font-medium text-foreground">
            <span>执行方</span>
            <TodoExecutorSelect options={executorOptions} value={executor} onChange={setExecutor} />
          </label>
          <label className="block space-y-1.5 text-sm font-medium text-foreground">
            <span>负责人</span>
            <Select value={ownerId || undefined} onValueChange={setOwnerId} disabled={owners.length === 0}>
              <SelectTrigger aria-label="选择负责人">
                <SelectValue placeholder={owners.length ? '请选择负责人' : '当前空间暂无可选成员'} />
              </SelectTrigger>
              <SelectContent>
                {owners.map((owner) => (
                  <SelectItem key={owner.userId} value={owner.userId}>
                    {owner.userName}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </label>
        </div>
        <ModalFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button
            loading={loading}
            disabled={!title.trim() || !executor || !ownerId}
            onClick={() => void submit()}
          >
            创建待办
          </Button>
        </ModalFooter>
      </ModalContent>
    </Modal>
  );
}
