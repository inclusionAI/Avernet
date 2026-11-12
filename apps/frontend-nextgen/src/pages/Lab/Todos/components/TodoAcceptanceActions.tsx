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
    <section
      role="region"
      aria-label="待办验收"
      className="rounded-2xl border border-warning/45 bg-warning/5 px-6 py-6 sm:px-8 sm:py-7"
    >
      <div className="flex items-center gap-3 text-xl font-semibold text-foreground">
        <Flag className="size-6 text-warning" aria-hidden />
        <span>待你验收 · Bot 已完成处理</span>
      </div>

      {rejecting ? (
        <div className="mt-5 space-y-4">
          <Textarea
            value={message}
            onChange={(event) => setMessage(event.target.value)}
            placeholder="填写需要 Bot 补充或修改的内容"
            rows={3}
            disabled={loading}
          />
          <div className="flex flex-wrap justify-end gap-2">
            <Button variant="ghost" onClick={() => setRejecting(false)} disabled={loading}>
              取消
            </Button>
            <Button
              variant="outline"
              onClick={() => void submit('rejected')}
              loading={loading}
              disabled={!message.trim()}
            >
              提交问题
            </Button>
          </div>
        </div>
      ) : (
        <div className="mt-5 flex flex-wrap gap-4">
          <Button className="h-11 rounded-xl px-5 text-base" variant="outline" onClick={() => setRejecting(true)} disabled={loading}>
            验收不通过
          </Button>
          <Button
            className="h-11 rounded-xl bg-foreground px-5 text-base text-background hover:opacity-85"
            onClick={() => void submit('approved')}
            loading={loading}
            leftIcon={<Check className="size-5" aria-hidden />}
          >
            验收通过
          </Button>
        </div>
      )}
    </section>
  );
}
