import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { Textarea } from '@/components/ui/Textarea';
import type { TodoEvent } from '@/domain/todos/models';
import { Send } from 'lucide-react';
import { useState } from 'react';

interface TodoInteractionPanelProps {
  interactions: TodoEvent[];
  botName?: string;
  enabled: boolean;
  loading: boolean;
  onSubmit: (message: string) => Promise<boolean>;
}

function formatDateTime(value: string): string {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { hour12: false });
}

function actorLabel(item: TodoEvent, botName?: string): string {
  if (item.actorType !== 'bot') return '我';
  return botName?.trim() || item.actorId?.trim() || 'Bot';
}

function actionLabel(item: TodoEvent): string {
  if (item.actorType === 'bot') return '完成结果';
  return item.eventType === 'task.created' ? '发起需求' : '我的反馈';
}

function interactionContent(item: TodoEvent): string {
  if (item.content) return item.content;
  if (item.eventType === 'task.completed') return '本轮执行已完成，但未上报文本结果。';
  return item.eventType;
}

export function TodoInteractionPanel({ interactions, botName, enabled, loading, onSubmit }: TodoInteractionPanelProps) {
  const [message, setMessage] = useState('');
  const submit = async () => {
    const success = await onSubmit(message);
    if (success) setMessage('');
  };
  return (
    <section role="region" aria-label="待办互动" className="space-y-3">
      <div className="text-sm font-semibold text-foreground">互动</div>
      {interactions.length > 0 ? (
        <div className="space-y-2">
          {interactions.map((item) => (
            <Card
              key={item.eventId || `${item.executionId}-${item.eventSeq}`}
              className="rounded-xl border border-border bg-card p-3"
            >
              <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
                <span className="flex min-w-0 items-center gap-2">
                  <span className="max-w-56 truncate font-medium text-foreground">{actorLabel(item, botName)}</span>
                  <span className="inline-flex shrink-0 rounded-md bg-muted/70 px-2 py-0.5">
                    {actionLabel(item)}
                  </span>
                </span>
                <span>{formatDateTime(item.createdAt)}</span>
              </div>
              <p className="m-0 mt-2 whitespace-pre-wrap break-words text-xs leading-5 text-foreground">
                {interactionContent(item)}
              </p>
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
              className="h-8 rounded-lg px-4 text-xs"
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
