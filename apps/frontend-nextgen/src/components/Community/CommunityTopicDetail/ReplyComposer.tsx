import { Button } from '@/components/ui/Button';
import { Textarea } from '@/components/ui/Textarea';
import { useState } from 'react';

interface ReplyComposerProps {
  replyTargetFloor: number | null;
  replyTargetPostId: string | null;
  publishing: boolean;
  onCancelReply: () => void;
  onSubmitReply: (body: string) => Promise<boolean>;
}

export function ReplyComposer({
  replyTargetFloor,
  replyTargetPostId,
  publishing,
  onCancelReply,
  onSubmitReply,
}: ReplyComposerProps) {
  const [draft, setDraft] = useState('');
  const [submitError, setSubmitError] = useState<string | null>(null);

  const submitReply = async () => {
    const body = draft.trim();
    if (!body) {
      setSubmitError('请输入回复内容');
      return;
    }
    const submitted = await onSubmitReply(body);
    if (submitted) {
      setDraft('');
      setSubmitError(null);
    }
  };

  return (
    <section className="py-4" aria-label="回复编辑区">
      <div className="mb-2 flex items-center justify-between gap-3 text-xs text-muted-foreground">
        <span>{replyTargetPostId ? `正在回复第 ${replyTargetFloor ?? '目标'} 楼` : '直接回复主题'}</span>
        {replyTargetPostId ? (
          <Button variant="ghost" size="sm" className="h-7 px-2.5" onClick={onCancelReply}>
            取消引用
          </Button>
        ) : null}
      </div>
      <Textarea
        className="min-h-[96px] rounded-lg border-border bg-card px-3 py-3 text-sm leading-relaxed"
        value={draft}
        onChange={(event) => {
          setDraft(event.target.value);
          setSubmitError(null);
        }}
        placeholder={replyTargetPostId ? '回复这个楼层...' : '回复这个主题...'}
        maxLength={20000}
        variant={submitError ? 'error' : 'default'}
        aria-label="回复正文"
        aria-invalid={!!submitError}
      />
      {submitError ? <p className="m-0 mt-2 text-xs text-destructive">{submitError}</p> : null}
      <Button
        className="mt-3 bg-foreground px-4 text-xs text-background hover:opacity-90"
        loading={publishing}
        onClick={() => void submitReply()}
      >
        {replyTargetPostId ? '发送楼层回复' : '发送回复'}
      </Button>
    </section>
  );
}
