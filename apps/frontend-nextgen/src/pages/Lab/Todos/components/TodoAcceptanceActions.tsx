import { Button } from '@/components/ui/Button';
import { Textarea } from '@/components/ui/Textarea';
import { useState } from 'react';

interface TodoAcceptanceActionsProps {
  loading: boolean;
  onAccept: (verdict: 'approved' | 'rejected', message?: string) => Promise<boolean>;
}

export function TodoAcceptanceActions({ loading, onAccept }: TodoAcceptanceActionsProps) {
  const [message, setMessage] = useState('');
  const submit = async (verdict: 'approved' | 'rejected') => {
    const success = await onAccept(verdict, message);
    if (success) setMessage('');
  };
  return (
    <section className="space-y-3 border-t border-divider pt-4">
      <div>
        <div className="text-sm font-semibold text-foreground">验收</div>
        <p className="m-0 mt-1 text-xs text-muted-foreground">通过后待办完成；不通过会记录意见并重新执行。</p>
      </div>
      <Textarea
        value={message}
        onChange={(event) => setMessage(event.target.value)}
        placeholder="可选：填写验收意见"
        rows={3}
        disabled={loading}
      />
      <div className="flex flex-wrap justify-end gap-2">
        <Button variant="outline" onClick={() => void submit('rejected')} loading={loading}>
          不通过并重做
        </Button>
        <Button onClick={() => void submit('approved')} loading={loading}>
          通过验收
        </Button>
      </div>
    </section>
  );
}
