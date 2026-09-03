import { Badge, Empty, Segmented } from '@/components/ui';
import { Modal, ModalContent, ModalHeader, ModalTitle } from '@/components/ui/Modal';
import type { Block, ChatMessage } from '@tc-chat/core';
import { FileText } from 'lucide-react';
import { useMemo, useState } from 'react';

export interface ResourceManageModalProps {
  sessionId: string | null;
  messages: ChatMessage[];
  onClose: () => void;
}

interface AttachmentItem {
  name: string;
  url?: string;
  source: string;
}

/**
 * 从消息块中提取附件项（防御式读取）。
 *
 * 已知 @tc-chat/core 并无 `attachment` 类型 Block；既有的文件型 Block 为 `file-card`
 * （字段：file_name / resource_id，无 url）。为兼容老协议与未来扩展，这里同时支持：
 * - `b.type === 'attachment'`：约定的附件块，读取 name/url（防御式）。
 * - `b.type === 'file-card'`：真实 FileCardBlock，name=file_name，url 缺省（resource_id 仅后端可用）。
 * - 顶层 `message.attachments[]`（MessageAttachment：fileName/mimeType/content）。
 */
function extractAttachments(messages: ChatMessage[]): AttachmentItem[] {
  const items: AttachmentItem[] = [];
  for (const msg of messages) {
    const blocks = (msg.blocks ?? []) as Block[];
    for (const b of blocks) {
      const block = b as unknown as Record<string, unknown>;
      const t = typeof block.type === 'string' ? block.type : '';
      if (t === 'attachment') {
        const name = typeof block.name === 'string' ? block.name : '';
        const url = typeof block.url === 'string' ? block.url : undefined;
        if (name) items.push({ name, url, source: 'attachment-block' });
      } else if (t === 'file-card') {
        const name =
          typeof block.file_name === 'string'
            ? block.file_name
            : typeof (block as { file_name?: unknown }).file_name === 'string'
            ? (block as { file_name: string }).file_name
            : '';
        if (name) items.push({ name, url: undefined, source: 'file-card' });
      }
    }
    const atts = msg.attachments ?? [];
    for (const a of atts) {
      if (a && typeof a.fileName === 'string' && a.fileName) {
        items.push({ name: a.fileName, url: undefined, source: 'message-attachment' });
      }
    }
  }
  return items;
}

export function ResourceManageModal({ sessionId, messages, onClose }: ResourceManageModalProps) {
  const items = useMemo(() => extractAttachments(messages), [messages]);
  const [filter, setFilter] = useState<'all' | 'file' | 'image'>('all');

  const filtered = useMemo(() => {
    if (filter === 'all') return items;
    return items;
  }, [items, filter]);

  return (
    <Modal open={sessionId !== null} onOpenChange={(o) => !o && onClose()}>
      <ModalContent size="lg" closeLabel="关闭资源管理弹窗">
        <ModalHeader>
          <ModalTitle className="m-0 flex items-center gap-2 text-base font-semibold text-foreground">
            资源管理
            <Badge tone="neutral">只读</Badge>
          </ModalTitle>
        </ModalHeader>

        {/* 过滤 */}
        <div>
          <Segmented<'all' | 'file' | 'image'>
            value={filter}
            onChange={setFilter}
            options={[
              { value: 'all', label: '全部' },
              { value: 'file', label: '文件' },
              { value: 'image', label: '图片' },
            ]}
          />
        </div>

        {/* 列表 */}
        <div className="max-h-[60vh] flex-1 overflow-y-auto">
          {filtered.length === 0 ? (
            <Empty title="当前会话暂无附件" compact />
          ) : (
            <ul className="m-0 list-none p-0">
              {filtered.map((item, idx) => (
                <li
                  key={`${item.name}-${idx}`}
                  className="flex items-center justify-between border-b border-border py-1.5 last:border-b-0"
                >
                  <div className="flex min-w-0 items-center gap-2">
                    <FileText aria-hidden className="h-4 w-4 shrink-0 text-muted-foreground" />
                    {item.url ? (
                      <a
                        href={item.url}
                        target="_blank"
                        rel="noreferrer noopener"
                        className="truncate text-sm text-primary hover:underline"
                      >
                        {item.name}
                      </a>
                    ) : (
                      <span className="truncate text-sm text-foreground">{item.name}</span>
                    )}
                  </div>
                  <Badge tone="neutral">{item.source}</Badge>
                </li>
              ))}
            </ul>
          )}
        </div>
      </ModalContent>
    </Modal>
  );
}

export default ResourceManageModal;
