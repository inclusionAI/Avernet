import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { Textarea } from '@/components/ui/Textarea';
import type { TodoEvent } from '@/domain/todos/models';
import { Send } from 'lucide-react';
import { useState } from 'react';

interface TodoInteractionPanelProps {
  interactions: TodoEvent[];
  enabled: boolean;
  loading: boolean;
  onSubmit: (message: string) => Promise<boolean>;
}

function formatDateTime(value: string): string {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { hour12: false });
}

export function TodoInteractionPanel({ interactions, enabled, loading, onSubmit }: TodoInteractionPanelProps) {
  const [message, setMessage] = useState('');
  const submit = async () => {
    const success = await onSubmit(message);
    if (success) setMessage('');
  };
  return (
    <section className="space-y-3">
      <div className="text-sm font-semibold text-foreground">互动</div>
      {interactions.length > 0 ? (
        <div className="space-y-2">
          {interactions.map((item) => (
            <Card
              key={item.eventId || `${item.executionId}-${item.eventSeq}`}
              className="rounded-lg bg-muted/20 p-3"
            >
              <div className="flex items-center justify-between gap-2 text-xs text-muted-foreground">
                <span>{item.actorType === 'bot' ? 'Bot' : '我'}</span>
                <span>{formatDateTime(item.createdAt)}</span>
              </div>
              <p className="m-0 mt-2 whitespace-pre-wrap text-xs leading-5 text-foreground">{item.content}</p>
            </Card>
          ))}
        </div>
      ) : (
        <p className="m-0 text-xs text-muted-foreground">暂无互动记录。</p>
      )}
      {enabled ? (
        <div className="space-y-2">
          <Textarea
            value={message}
            onChange={(event) => setMessage(event.target.value)}
            placeholder="告诉 Bot 需要补充或修改的内容"
            rows={4}
            disabled={loading}
          />
          <div className="flex justify-end">
            <Button
              onClick={() => void submit()}
              loading={loading}
              disabled={!message.trim()}
              leftIcon={<Send className="size-4" aria-hidden />}
            >
              发送互动
            </Button>
          </div>
        </div>
      ) : (
        <p className="m-0 rounded-md bg-muted/40 px-3 py-2 text-xs text-muted-foreground">仅待验收状态可以互动。</p>
      )}
    </section>
  );
}
