import { Button } from '@/components/ui/Button';
import { Textarea } from '@/components/ui/Textarea';
import type { CommunityTopic, CreateCommunityReplyDraft } from '@/domain/community/types';
import { MessageSquare } from 'lucide-react';
import { useState } from 'react';

export interface ReplyComposerTarget {
  postId: string;
  authorName: string;
  preview?: string;
}

export type ReplyComposerPayload = CreateCommunityReplyDraft;

interface ReplyComposerProps {
  topic: CommunityTopic;
  placeholder?: string;
  pollSelection: number[];
  onPollChange: (next: number[]) => void;
  submitLabel?: string;
  replyTo?: ReplyComposerTarget;
  saving?: boolean;
  disabled?: boolean;
  onSubmit: (payload: ReplyComposerPayload) => Promise<boolean>;
  onCancel?: () => void;
}

function togglePollSelection(current: number[], index: number, selectionType: 'single' | 'multi'): number[] {
  if (selectionType === 'single') return current[0] === index ? [] : [index];
  if (current.includes(index)) return current.filter((item) => item !== index);
  return [...current, index];
}

/**
 * 主题回复编辑器：`single`/`multi` 投票同时支持选择选项；
 * 楼中楼回复通过 `replyToPostId` 指向被回复楼层。
 */
export function ReplyComposer({
  topic,
  pollSelection,
  onPollChange,
  placeholder = '发表你的评论…',
  submitLabel = '回复',
  replyTo,
  saving = false,
  disabled = false,
  onSubmit,
  onCancel,
}: ReplyComposerProps) {
  const [body, setBody] = useState('');

  const poll = topic.topicType === 'poll' ? topic.topicConfig?.poll : undefined;

  const submit = async () => {
    if (!body.trim()) return;
    const payload: ReplyComposerPayload = {
      body: body.trim(),
      ...(poll ? { pollSelection } : {}),
      ...(replyTo ? { replyToPostId: replyTo.postId } : {}),
    };
    const succeeded = await onSubmit(payload);
    if (succeeded) {
      setBody('');
      onPollChange([]);
    }
  };

  return (
    <section aria-label={replyTo ? `回复 ${replyTo.authorName}` : '回复主题'} className="bg-background">
      {poll ? (
        <div aria-label="选择投票选项" className="mb-2 flex flex-wrap gap-2">
          {poll.options.map((option, index) => {
            const selected = pollSelection.includes(index);
            return (
              <Button
                key={`${option}-${index}`}
                type="button"
                size="sm"
                variant={selected ? 'secondary' : 'outline'}
                aria-pressed={selected}
                onClick={() => onPollChange(togglePollSelection(pollSelection, index, poll.selection))}
              >
                {option}
              </Button>
            );
          })}
        </div>
      ) : null}
      {replyTo && (
        <p className="m-0 mb-2 truncate text-xs text-muted-foreground">
          回复 <span className="font-medium text-foreground">@{replyTo.authorName}</span>
          {replyTo.preview && `：${replyTo.preview}`}
        </p>
      )}
      <Textarea
        autoFocus
        value={body}
        onChange={(event) => setBody(event.target.value)}
        placeholder={placeholder}
        aria-label={replyTo ? `回复 ${replyTo.authorName}` : '回复正文'}
        disabled={disabled || saving}
        variant="default"
        className="h-10 min-h-10 resize-none text-sm focus-visible:border-primary focus-visible:ring-0"
      />
      <footer className="mt-2 flex items-center justify-end gap-2">
        {onCancel ? (
          <Button variant="outline" size="sm" disabled={saving} onClick={onCancel}>
            取消
          </Button>
        ) : null}
        <Button
          variant="primary"
          size="sm"
          leftIcon={<MessageSquare className="size-3.5" aria-hidden />}
          loading={saving}
          disabled={disabled || !body.trim() || (Boolean(poll) && pollSelection.length === 0)}
          onClick={() => void submit()}
        >
          {submitLabel}
        </Button>
      </footer>
    </section>
  );
}
