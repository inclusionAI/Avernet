import { useState } from 'react';
import { Button } from '@/components/ui/Button';
import { useExpertBotCreation } from '@/hooks/useExpertBotCreation';
import { ExpertCreationResult } from './CreateBotModal/ExpertCreationResult';

function RecoveryReport({ botId, onClose }: { botId: string; onClose: () => void }) {
  const creation = useExpertBotCreation(true, botId);
  return (
    <div className="mt-3">
      <ExpertCreationResult botId={botId} status={creation.status} error={creation.error}
        unknown={false} onClose={onClose} onRecover={creation.recover} recovering={creation.recovering} />
    </div>
  );
}

/** Reopens the same creation/Apply facts from Bot management after modal exit. */
export function ExpertConfigurationRecovery({ botId }: { botId: string }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="border-b border-border px-6 py-3">
      <Button type="button" variant="secondary" onClick={() => setOpen(!open)}>专家配置与恢复</Button>
      {open && <RecoveryReport key={botId} botId={botId} onClose={() => setOpen(false)} />}
    </div>
  );
}
