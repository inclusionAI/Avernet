import { MessageViewScopeField } from '@/components/MessageViewScope';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Modal, ModalContent, ModalHeader, ModalTitle } from '@/components/ui/Modal';
import { Textarea } from '@/components/ui/Textarea';
import { DEFAULT_MESSAGE_VIEW_SCOPE } from '@/domain/collaboration/messageViewScope';
import type { MessageViewScope } from '@/domain/collaboration/types';
import type { PublicGroup } from '@/domain/collaborationSquare/types';
import { MessagesSquare } from 'lucide-react';
import type { FormEvent } from 'react';
import { useEffect, useState } from 'react';

/** 创建公开协作群会话的表单值（对应接口 body：title + input.query + message_view_scope）。 */
export interface CreateGroupSessionFormValues {
  title: string;
  query: string;
  messageViewScope: MessageViewScope;
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
 * 表单样式对齐「创建云端 Bot」（CreateBotModal）：图标题头 + label 包裹字段 +
 * 必填星号 + 字数计数器 + 行内 secondary 取消/primary 提交；不再展示描述提示文案。
 * 仅 UI：表单状态本地维护，提交与跳转编排交给 Hook（Component → Hook → Service 分层）。
 */
export function CreateGroupSessionModal({ open, group, loading, onClose, onSubmit }: CreateGroupSessionModalProps) {
  const [title, setTitle] = useState('');
  const [query, setQuery] = useState('');
  const [viewScope, setViewScope] = useState<MessageViewScope>(DEFAULT_MESSAGE_VIEW_SCOPE);

  // 弹窗打开/切换目标群时重置表单，避免上一群残留输入。
  useEffect(() => {
    if (open) {
      setTitle('');
      setQuery('');
      setViewScope(DEFAULT_MESSAGE_VIEW_SCOPE);
    }
  }, [open, group?.id]);

  const trimmedTitle = title.trim();
  const trimmedQuery = query.trim();
  const canSubmit = !loading && trimmedTitle !== '' && trimmedQuery !== '';

  const handleSubmit = (event: FormEvent) => {
    event.preventDefault();
    if (!canSubmit) return;
    onSubmit({ title: trimmedTitle, query: trimmedQuery, messageViewScope: viewScope });
  };

  return (
    <Modal open={open} onOpenChange={(next) => !next && onClose()}>
      <ModalContent size="sm" aria-describedby={undefined} className="p-4">
        <ModalHeader className="flex-row items-center gap-2.5 space-y-0">
          <div className="flex size-10 shrink-0 items-center justify-center rounded-lg bg-primary text-primary-foreground">
            <MessagesSquare aria-hidden className="size-5" />
          </div>
          <div className="min-w-0">
            <ModalTitle className="text-base leading-6">
              {group ? `在「${group.name}」创建新会话` : '创建新会话'}
            </ModalTitle>
          </div>
        </ModalHeader>
        <form className="space-y-5" onSubmit={handleSubmit}>
          <label className="block space-y-2 text-xs font-medium text-foreground">
            <span className="flex items-center justify-between gap-2">
              <span>
                会话名称 <span className="text-destructive">*</span>
              </span>
              <span className="text-[10px] font-normal text-muted-foreground">{title.length}/100</span>
            </span>
            <Input
              autoFocus
              value={title}
              maxLength={100}
              placeholder="请输入会话名称"
              disabled={loading}
              className="focus-visible:border-ring focus-visible:ring-1 focus-visible:ring-ring/30 focus-visible:ring-offset-0"
              onChange={(event) => setTitle(event.target.value)}
            />
          </label>
          <label className="block space-y-2 text-xs font-medium text-foreground">
            <span className="flex items-center justify-between gap-2">
              <span>
                协作目标 <span className="text-destructive">*</span>
              </span>
              <span className="text-[10px] font-normal text-muted-foreground">{query.length}/2000</span>
            </span>
            <Textarea
              rows={4}
              value={query}
              maxLength={2000}
              placeholder="请描述本会话希望达成的协作目标"
              disabled={loading}
              className="focus-visible:border-ring focus-visible:ring-1 focus-visible:ring-ring/30 focus-visible:ring-offset-0"
              onChange={(event) => setQuery(event.target.value)}
            />
          </label>
          <MessageViewScopeField value={viewScope} onChange={setViewScope} disabled={loading} />
          <div className="flex items-center justify-end gap-2">
            <Button type="button" variant="secondary" disabled={loading} onClick={onClose}>
              取消
            </Button>
            <Button type="submit" loading={loading} disabled={!canSubmit}>
              创建会话
            </Button>
          </div>
        </form>
      </ModalContent>
    </Modal>
  );
}
