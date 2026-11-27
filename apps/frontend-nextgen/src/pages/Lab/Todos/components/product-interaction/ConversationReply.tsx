import { Button, Textarea } from '@/components/ui';
import { cn } from '@/utils/cn';
import { Send } from 'lucide-react';

interface ConversationReplyProps {
  value: string;
  disabled: boolean;
  loading: boolean;
  onChange: (value: string) => void;
  onSend: () => void;
}

export function ConversationReply({ value, disabled, loading, onChange, onSend }: ConversationReplyProps) {
  return (
    <div className="mt-3 border-t border-divider pt-3">
      <div className={cn('rounded-lg border border-border bg-background px-3 py-2 focus-within:border-primary', disabled && 'opacity-60')}>
        <Textarea
          value={value}
          onChange={(event) => onChange(event.target.value)}
          onKeyDown={(event) => {
            if (event.key !== 'Enter' || event.shiftKey || event.nativeEvent.isComposing) return;
            event.preventDefault();
            onSend();
          }}
          disabled={disabled}
          placeholder={disabled ? '待办助手执行中，暂无法回复…' : '回复，与待办助手继续对话；Enter 发送，Shift+Enter 换行'}
          rows={2}
          className="min-h-0 resize-none border-0 bg-transparent p-0 shadow-none focus-visible:ring-0"
          aria-label="继续当前互动"
        />
        <div className="mt-1 flex justify-end">
          <Button
            type="button"
            variant="primary"
            size="icon"
            className="size-7 rounded-lg"
            loading={loading}
            disabled={disabled || !value.trim()}
            aria-label="发送回复"
            onClick={onSend}
          >
            <Send className="size-3.5" aria-hidden />
          </Button>
        </div>
      </div>
    </div>
  );
}
