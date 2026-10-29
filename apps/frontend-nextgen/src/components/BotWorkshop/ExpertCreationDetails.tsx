import { useState } from 'react';
import { Button } from '@/components/ui/Button';
import { useExpertBotCreation } from '@/hooks/useExpertBotCreation';
import { ExpertCreationResult } from './CreateBotModal/ExpertCreationResult';

function CreationReport({ botId, onClose }: { botId: string; onClose: () => void }) {
  const creation = useExpertBotCreation(true, botId);
  return (
    <div className="mt-3">
      <ExpertCreationResult botId={botId} status={creation.status} error={creation.error}
        unknown={false} onClose={onClose} />
    </div>
  );
}

/** Reads the original creation result and failure report from Bot management after modal exit. */
export function ExpertCreationDetails({ botId }: { botId: string }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="border-b border-border px-6 py-3">
      <Button type="button" variant="secondary" onClick={() => setOpen(!open)}>专家创建详情</Button>
      {open && <CreationReport key={botId} botId={botId} onClose={() => setOpen(false)} />}
    </div>
  );
}
