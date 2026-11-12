import { Button } from '@/components/ui/Button';
import { Textarea } from '@/components/ui/Textarea';
import { Check, Flag } from 'lucide-react';
import { useState } from 'react';

interface TodoAcceptanceActionsProps {
  loading: boolean;
  onAccept: (verdict: 'approved' | 'rejected', message?: string) => Promise<boolean>;
}

export function TodoAcceptanceActions({ loading, onAccept }: TodoAcceptanceActionsProps) {
  const [message, setMessage] = useState('');
  const [rejecting, setRejecting] = useState(false);

  const submit = async (verdict: 'approved' | 'rejected') => {
    const success = await onAccept(verdict, message);
    if (success) {
      setMessage('');
      setRejecting(false);
    }
  };

  return (
    <section role="region" aria-label="待办验收" className="rounded-xl border border-warning/45 bg-warning/5 p-4">
      <h2 className="m-0 flex items-center gap-2 text-sm font-semibold text-foreground">
        <Flag className="size-4 shrink-0 text-warning" aria-hidden />
        <span>待你验收 · Bot 已完成处理</span>
      </h2>

      {rejecting ? (
        <div className="mt-3 space-y-3">
          <Textarea
            value={message}
            onChange={(event) => setMessage(event.target.value)}
            placeholder="填写需要 Bot 补充或修改的内容"
            rows={3}
            disabled={loading}
          />
          <div className="flex flex-wrap justify-end gap-2">
            <Button variant="ghost" className="h-8 rounded-lg px-4 text-xs" onClick={() => setRejecting(false)} disabled={loading}>
              取消
            </Button>
            <Button
              variant="outline"
              className="h-8 rounded-lg px-4 text-xs"
              onClick={() => void submit('rejected')}
              loading={loading}
              disabled={!message.trim()}
            >
              提交问题
            </Button>
          </div>
        </div>
      ) : (
        <div className="mt-3 flex flex-wrap gap-2">
          <Button
            className="h-8 rounded-lg px-4 text-xs"
            variant="outline"
            onClick={() => setRejecting(true)}
            disabled={loading}
          >
            验收不通过
          </Button>
          <Button
            className="h-8 rounded-lg bg-foreground px-4 text-xs text-background hover:opacity-85"
            onClick={() => void submit('approved')}
            loading={loading}
            leftIcon={<Check className="size-4" aria-hidden />}
          >
            验收通过
          </Button>
        </div>
      )}
    </section>
  );
}
