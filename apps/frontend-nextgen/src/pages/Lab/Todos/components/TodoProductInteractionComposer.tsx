import { Button, Textarea } from '@/components/ui';
import type { CreateTodoProductInteractionInput, TodoStatus } from '@/domain/todos/models';
import { canInteractWithTodo } from '@/domain/todos/status';
import { cn } from '@/utils/cn';
import { Send } from 'lucide-react';
import { useEffect, useState } from 'react';

interface TodoProductInteractionComposerProps {
  todoId: string;
  canSend: boolean;
  todoStatus: TodoStatus;
  loading: boolean;
  onSubmit: (input: CreateTodoProductInteractionInput) => Promise<boolean> | boolean;
}

export function TodoProductInteractionComposer({
  todoId,
  canSend,
  todoStatus,
  loading,
  onSubmit,
}: TodoProductInteractionComposerProps) {
  const [message, setMessage] = useState('');
  const statusAllowsInteraction = canInteractWithTodo(todoStatus);
  const sendDisabled = loading || !canSend || !statusAllowsInteraction;

  useEffect(() => {
    setMessage('');
  }, [todoId]);

  const submit = async () => {
    const text = message.trim();
    if (!text || sendDisabled) return;
    const accepted = await onSubmit({ message: text });
    if (accepted) setMessage('');
  };

  if (!statusAllowsInteraction) {
    return <p className="m-0 rounded-md bg-muted/40 px-3 py-2 text-xs text-muted-foreground">当前待办状态不允许继续互动。</p>;
  }

  return (
    <section
      role="region"
      aria-label="发起 Product 待办互动"
      className={cn('rounded-xl border border-border bg-card px-3 py-2 focus-within:border-primary', sendDisabled && 'opacity-60')}
    >
      <Textarea
        value={message}
        onChange={(event) => setMessage(event.target.value)}
        onKeyDown={(event) => {
          if (event.key !== 'Enter' || event.shiftKey || event.nativeEvent.isComposing) return;
          event.preventDefault();
          void submit();
        }}
        placeholder={sendDisabled ? '待办助手执行中，暂无法评论…' : '评论，与待办助手对话；Enter 发送，Shift+Enter 换行'}
        rows={2}
        disabled={sendDisabled}
        className="min-h-0 resize-none border-0 bg-transparent p-0 shadow-none focus-visible:ring-0"
        aria-label="评论，与待办助手对话"
      />
      <div className="mt-1 flex justify-end">
        <Button
          type="button"
          variant="primary"
          size="icon"
          className="size-8 rounded-lg"
          loading={loading}
          disabled={sendDisabled || !message.trim()}
          aria-label="发送评论"
          onClick={() => void submit()}
        >
          <Send className="size-4" aria-hidden />
        </Button>
      </div>
    </section>
  );
}
