import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Modal, ModalContent, ModalDescription, ModalFooter, ModalHeader, ModalTitle } from '@/components/ui/Modal';
import { Textarea } from '@/components/ui/Textarea';
import type { PublicGroup } from '@/domain/collaborationSquare/types';
import { useEffect, useState } from 'react';

/** 创建公开协作群会话的表单值（对应接口 body：title + input.query）。 */
export interface CreateGroupSessionFormValues {
  title: string;
  query: string;
}

export interface CreateGroupSessionModalProps {
  open: boolean;
  group: PublicGroup | null;
  loading: boolean;
  onClose: () => void;
  onSubmit: (values: CreateGroupSessionFormValues) => void;
}

/**
 * 「公开协作群 → 创建新会话」表单弹窗：收集「会话名称」(title) 与「协作目标」(input.query)，
 * 提交后由上层 Hook 调 POST /openapi/v1/collaboration/groups/{group_id}/sessions 并跳转。
 *
 * 仅 UI：表单状态本地维护，提交与跳转编排交给 Hook（Component → Hook → Service 分层）。
 */
export function CreateGroupSessionModal({ open, group, loading, onClose, onSubmit }: CreateGroupSessionModalProps) {
  const [title, setTitle] = useState('');
  const [query, setQuery] = useState('');

  // 弹窗打开/切换目标群时重置表单，避免上一群残留输入。
  useEffect(() => {
    if (open) {
      setTitle('');
      setQuery('');
    }
  }, [open, group?.id]);

  const trimmedTitle = title.trim();
  const trimmedQuery = query.trim();
  const canSubmit = !loading && trimmedTitle !== '' && trimmedQuery !== '';

  const handleSubmit = () => {
    if (!canSubmit) return;
    onSubmit({ title: trimmedTitle, query: trimmedQuery });
  };

  return (
    <Modal open={open} onOpenChange={(next) => !next && onClose()}>
      <ModalContent size="sm">
        <ModalHeader>
          <ModalTitle>{group ? `在「${group.name}」创建新会话` : '创建新会话'}</ModalTitle>
          <ModalDescription>填写会话名称与协作目标，创建后跳转到该会话。</ModalDescription>
        </ModalHeader>
        <div className="space-y-4">
          <div className="space-y-2">
            <label htmlFor="create-session-title" className="m-0 text-sm font-medium text-foreground">
              会话名称
            </label>
            <Input
              id="create-session-title"
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              placeholder="请输入会话名称"
              maxLength={100}
              disabled={loading}
              autoFocus
            />
          </div>
          <div className="space-y-2">
            <label htmlFor="create-session-query" className="m-0 text-sm font-medium text-foreground">
              协作目标
            </label>
            <Textarea
              id="create-session-query"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="请描述本会话希望达成的协作目标"
              rows={4}
              maxLength={2000}
              disabled={loading}
            />
          </div>
        </div>
        <ModalFooter>
          <Button variant="ghost" onClick={onClose} disabled={loading}>
            取消
          </Button>
          <Button onClick={handleSubmit} loading={loading} disabled={!canSubmit}>
            创建会话
          </Button>
        </ModalFooter>
      </ModalContent>
    </Modal>
  );
}
