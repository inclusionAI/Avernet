import {
  Button,
  Empty,
  Modal,
  ModalContent,
  ModalDescription,
  ModalFooter,
  ModalHeader,
  ModalTitle,
  Spin,
} from '@/components/ui';
import type {
  CodingBotSessionArchiveItem,
  CodingBotSessionArchivePreviewMessage,
  CodingBotSessionArchivePreviewResult,
} from '@/domain/conversation/types';
import { cn } from '@/utils/cn';
import { Bot, MessageSquareText, UserRound } from 'lucide-react';
import {
  archiveMessageCountLabel,
  archiveTitle,
  formatMessageTime,
} from './ConversationSessionArchivePresentation';

function PreviewMessage({ message }: { message: CodingBotSessionArchivePreviewMessage }) {
  const isUser = message.role === 'user';
  const time = formatMessageTime(message.timestamp);
  return (
    <article className="flex gap-2.5 rounded-lg bg-muted/40 px-3 py-2.5">
      <div
        className={cn(
          'mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-md',
          isUser ? 'bg-background text-muted-foreground ring-1 ring-border' : 'bg-primary/10 text-primary',
        )}
        aria-hidden="true"
      >
        {isUser ? <UserRound className="h-3.5 w-3.5" /> : <Bot className="h-3.5 w-3.5" />}
      </div>
      <div className="min-w-0 flex-1">
        <div className="mb-1 flex items-center gap-2 text-[10px] text-content-hint">
          <span className="font-medium text-content-soft">{isUser ? '用户' : '助手'}</span>
          {time && <time dateTime={message.timestamp || undefined}>{time}</time>}
        </div>
        <p className="whitespace-pre-wrap break-words text-sm leading-5 text-content-strong">{message.text}</p>
      </div>
    </article>
  );
}

function PreviewSection({
  label,
  messages,
}: {
  label: string;
  messages: CodingBotSessionArchivePreviewMessage[];
}) {
  if (messages.length === 0) return null;
  return (
    <section aria-label={label} className="space-y-1.5">
      <div className="flex items-center gap-2">
        <span className="text-[11px] font-medium text-content-soft">{label}</span>
        <span className="text-[10px] text-content-hint">{messages.length} 条</span>
        <span className="h-px flex-1 bg-border" aria-hidden="true" />
      </div>
      <div className="space-y-1.5">
        {messages.map((message, index) => (
          <PreviewMessage key={`${message.role}-${message.timestamp || ''}-${index}`} message={message} />
        ))}
      </div>
    </section>
  );
}

export interface ConversationSessionArchivePreviewDialogProps {
  item: CodingBotSessionArchiveItem | null;
  result: CodingBotSessionArchivePreviewResult | null;
  loading: boolean;
  error: string | null;
  restoreSupported: boolean;
  submitting: boolean;
  onOpenChange(open: boolean): void;
  onRestore(item: CodingBotSessionArchiveItem): Promise<void>;
}

export function ConversationSessionArchivePreviewDialog({
  item,
  result,
  loading,
  error,
  restoreSupported,
  submitting,
  onOpenChange,
  onRestore,
}: ConversationSessionArchivePreviewDialogProps) {
  const firstMessages = result?.ok ? result.first : [];
  const lastMessages = result?.ok ? result.last : [];
  const samePreview =
    firstMessages.length === lastMessages.length &&
    firstMessages.every(
      (message, index) =>
        message.role === lastMessages[index]?.role &&
        message.text === lastMessages[index]?.text &&
        message.timestamp === lastMessages[index]?.timestamp,
    );
  const previewSections = samePreview
    ? [{ label: '消息预览', messages: firstMessages }]
    : [
        { label: '开头消息', messages: firstMessages },
        { label: '最近消息', messages: lastMessages },
      ];
  const hasPreviewMessages = firstMessages.length > 0 || lastMessages.length > 0;
  const handleRestore = async (target: CodingBotSessionArchiveItem) => {
    await onRestore(target);
    onOpenChange(false);
  };

  return (
    <Modal open={item !== null} onOpenChange={onOpenChange}>
      <ModalContent size="lg" className="max-h-[80vh] overflow-y-auto">
        <ModalHeader className="mb-4">
          <div className="flex items-start gap-3">
            <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-muted text-muted-foreground">
              <MessageSquareText className="h-[17px] w-[17px]" />
            </div>
            <div className="min-w-0">
              <ModalTitle className="truncate">{item ? archiveTitle(item) : '会话预览'}</ModalTitle>
              <ModalDescription>快速查看开头和最近消息；预览不会修改会话。</ModalDescription>
              {item && <p className="mt-1 text-xs text-content-soft">{archiveMessageCountLabel(item)}</p>}
            </div>
          </div>
        </ModalHeader>
        {loading ? (
          <Spin tip="消息摘要加载中…" className="min-h-24 py-0" />
        ) : error ? (
          <p role="alert" className="rounded-lg bg-destructive/10 px-3 py-2.5 text-sm text-destructive">
            {error}
          </p>
        ) : !result?.ok ? (
          <Empty compact title="此会话暂无可预览的正文消息" />
        ) : !result.available ? (
          <Empty compact title="此旧归档暂无预览，不影响恢复" />
        ) : !hasPreviewMessages ? (
          <Empty
            compact
            title={result.incomplete ? '暂无法提取消息摘要，不影响恢复' : '此会话暂无可预览的正文消息'}
          />
        ) : (
          <div className="space-y-4">
            {previewSections.map((section) => (
              <PreviewSection key={section.label} label={section.label} messages={section.messages} />
            ))}
            {result.incomplete && (
              <p className="text-xs text-content-hint">部分消息过大，未纳入预览；恢复仍保留完整原始数据。</p>
            )}
          </div>
        )}
        {item &&
          restoreSupported &&
          (item.restoreStatus === 'archived' || item.restoreStatus === 'failed') && (
            <ModalFooter className="mt-4">
              <Button
                variant="secondary"
                size="sm"
                loading={submitting}
                disabled={submitting}
                onClick={() => void handleRestore(item)}
              >
                {item.restoreStatus === 'failed' ? '重试恢复' : '恢复会话'}
              </Button>
            </ModalFooter>
          )}
      </ModalContent>
    </Modal>
  );
}
