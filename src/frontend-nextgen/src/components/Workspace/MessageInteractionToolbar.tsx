import { Check, CircleAlert, Copy, Lightbulb, LoaderCircle, MessageSquareQuote, Pencil, Square, X } from 'lucide-react';
import type React from 'react';
import { useEffect, useRef, useState } from 'react';

import { IconButton } from '@/components/ui';
import type { MessageQuote, MessageSelection } from '@/pages/Workspace/hooks/useMessageInteractions';

interface MessageInteractionToolbarProps {
  onCopy: () => void | boolean | Promise<void | boolean>;
  onEdit?: () => void;
  isEditable?: boolean;
  isStreaming?: boolean;
  onStop?: () => void;
  className?: string;
  showCopy?: boolean;
}

function handleKeyboardAction(event: React.KeyboardEvent<HTMLButtonElement>, action: () => void) {
  if (event.key !== 'Enter' && event.key !== ' ') return;
  event.preventDefault();
  action();
}

export function MessageInteractionToolbar({
  onCopy,
  onEdit,
  isEditable,
  isStreaming,
  onStop,
  className,
  showCopy = true,
}: MessageInteractionToolbarProps) {
  return (
    <div
      className={`flex items-center gap-0.5 pt-1 text-muted-foreground ${className ?? ''}`}
      role="toolbar"
      aria-label="消息操作"
    >
      {isEditable && onEdit ? (
        <IconButton
          label="编辑消息"
          size="sm"
          icon={<Pencil className="h-3.5 w-3.5" />}
          onClick={onEdit}
          onKeyDown={(event) => handleKeyboardAction(event, onEdit)}
        />
      ) : null}
      {showCopy ? (
        <IconButton
          label="复制消息"
          size="sm"
          icon={<Copy className="h-3.5 w-3.5" />}
          onClick={() => void onCopy()}
          onKeyDown={(event) => handleKeyboardAction(event, () => void onCopy())}
        />
      ) : null}
      {isStreaming && onStop ? (
        <IconButton
          label="停止生成"
          size="sm"
          icon={<Square className="h-3 w-3 fill-current" />}
          onClick={onStop}
          onKeyDown={(event) => handleKeyboardAction(event, onStop)}
        />
      ) : null}
    </div>
  );
}

interface MessageCopyActionProps {
  onCopy?: () => void | boolean | Promise<void | boolean>;
  align: 'left' | 'right';
  testId?: string;
  onEdit?: () => void;
  isEditable?: boolean;
}

const COPY_FEEDBACK_MS = 1600;

/**
 * 消息末尾的常驻操作入口。编辑与复制都直接展示，避免依赖 hover 才能发现；
 * 每个 IconButton 继续通过统一 Tooltip 提供 Codex 风格的悬停文案。
 */
export function MessageCopyAction({ onCopy, align, testId, onEdit, isEditable }: MessageCopyActionProps) {
  const [copyFeedback, setCopyFeedback] = useState<'pending' | 'success' | 'error' | null>(null);
  const feedbackTimerRef = useRef<ReturnType<typeof setTimeout>>();
  const alignmentClass = align === 'right' ? 'justify-end pr-11' : 'justify-start pl-11';

  useEffect(() => () => clearTimeout(feedbackTimerRef.current), []);

  const handleCopy = async () => {
    if (!onCopy) return;
    setCopyFeedback('pending');
    try {
      const result = await onCopy();
      const feedback = result === false ? 'error' : 'success';
      setCopyFeedback(feedback);
      clearTimeout(feedbackTimerRef.current);
      feedbackTimerRef.current = setTimeout(() => setCopyFeedback(null), COPY_FEEDBACK_MS);
    } catch {
      // 复制失败的详细原因由消息交互 Hook 负责；此处补充按钮内的失败反馈。
      setCopyFeedback('error');
      clearTimeout(feedbackTimerRef.current);
      feedbackTimerRef.current = setTimeout(() => setCopyFeedback(null), COPY_FEEDBACK_MS);
    }
  };

  return (
    <div data-testid={testId} className={`relative z-10 mt-0 flex ${alignmentClass}`}>
      <div className="flex items-center gap-0.5 pt-1 text-muted-foreground" role="toolbar" aria-label="消息底部操作">
        {isEditable && onEdit ? (
          <IconButton
            label="编辑消息"
            size="sm"
            icon={<Pencil className="h-3.5 w-3.5" />}
            onClick={onEdit}
            onKeyDown={(event) => handleKeyboardAction(event, onEdit)}
          />
        ) : null}
        {onCopy ? (
          <IconButton
            label={
              copyFeedback === 'pending'
                ? '复制中'
                : copyFeedback === 'success'
                ? '已复制'
                : copyFeedback === 'error'
                ? '复制失败'
                : '复制整条消息'
            }
            disabled={copyFeedback === 'pending'}
            size="sm"
            className={
              copyFeedback === 'success' ? 'text-success' : copyFeedback === 'error' ? 'text-destructive' : undefined
            }
            icon={
              copyFeedback === 'pending' ? (
                <LoaderCircle className="h-3.5 w-3.5 animate-spin" />
              ) : copyFeedback === 'success' ? (
                <Check className="h-3.5 w-3.5" />
              ) : copyFeedback === 'error' ? (
                <CircleAlert className="h-3.5 w-3.5" />
              ) : (
                <Copy className="h-3.5 w-3.5" />
              )
            }
            onClick={() => void handleCopy()}
            onKeyDown={(event) => handleKeyboardAction(event, () => void handleCopy())}
          />
        ) : null}
        {copyFeedback ? (
          <span
            data-testid={testId ? `${testId}-feedback` : undefined}
            className={
              copyFeedback === 'error'
                ? 'text-xs text-destructive'
                : copyFeedback === 'success'
                ? 'text-xs text-success'
                : 'text-xs text-muted-foreground'
            }
            role="status"
            aria-live="polite"
          >
            {copyFeedback === 'pending' ? '复制中' : copyFeedback === 'success' ? '已复制' : '复制失败'}
          </span>
        ) : null}
      </div>
    </div>
  );
}

interface MessageSelectionToolbarProps {
  selection: MessageSelection | null;
  onCopy: (text: string) => void | boolean | Promise<void | boolean>;
  onQuote: (text: string) => void;
  onExplain?: (text: string) => void;
}

export function MessageSelectionToolbar({ selection, onCopy, onQuote, onExplain }: MessageSelectionToolbarProps) {
  if (!selection) return null;
  return (
    <div
      className="z-20 flex items-center gap-0.5 rounded-lg border border-border bg-card p-1 shadow-md"
      role="toolbar"
      aria-label="文本选择操作"
      style={{ position: 'fixed', left: selection.rect.left, top: Math.max(8, selection.rect.top - 44) }}
    >
      <IconButton
        label="复制选中文本"
        size="sm"
        icon={<Copy className="h-3.5 w-3.5" />}
        onClick={() => void onCopy(selection.text)}
      />
      <IconButton
        label="追问选中文本"
        size="sm"
        icon={<MessageSquareQuote className="h-3.5 w-3.5" />}
        onClick={() => onQuote(selection.text)}
      />
      {onExplain ? (
        <IconButton
          label="解释选中文本"
          size="sm"
          icon={<Lightbulb className="h-3.5 w-3.5" />}
          onClick={() => onExplain(selection.text)}
        />
      ) : null}
    </div>
  );
}

interface MessageQuoteBarProps {
  quote: MessageQuote | null;
  onClear: () => void;
}

const composerContextBarClassName =
  'relative z-10 flex min-w-0 shrink-0 gap-2 rounded-md border border-border bg-muted px-2 py-1.5 text-xs text-muted-foreground';

export function MessageQuoteBar({ quote, onClear }: MessageQuoteBarProps) {
  if (!quote) return null;
  return (
    <div className={`${composerContextBarClassName} items-start`}>
      <MessageSquareQuote className="mt-0.5 h-3.5 w-3.5 shrink-0 text-primary" aria-hidden="true" />
      <div className="min-w-0 flex-1 overflow-hidden">
        <div className="truncate font-medium text-foreground">引用 {quote.senderName}</div>
        <div className="mt-0.5 line-clamp-2 whitespace-pre-wrap break-words">{quote.text}</div>
      </div>
      <IconButton
        label="取消引用"
        size="sm"
        className="shrink-0"
        icon={<X className="h-3.5 w-3.5" />}
        onClick={onClear}
      />
    </div>
  );
}

interface MessageEditBarProps {
  onCancel: () => void;
}

export function MessageEditBar({ onCancel }: MessageEditBarProps) {
  return (
    <div className={`${composerContextBarClassName} items-center`} role="status" aria-label="编辑消息状态">
      <Pencil className="h-3.5 w-3.5 shrink-0 text-primary" aria-hidden="true" />
      <div className="min-w-0 flex-1">
        <div className="font-medium text-foreground">正在编辑最近一条消息</div>
        <div className="mt-0.5 truncate">修改后点击发送，将作为新消息发送</div>
      </div>
      <IconButton label="取消编辑" size="sm" icon={<X className="h-3.5 w-3.5" />} onClick={onCancel} />
    </div>
  );
}
