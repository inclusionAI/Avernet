import { redactRunEvidence } from '@/services/taskEscortParity/runLaunchDetails';
import { EvidenceValue } from './OverviewShared';

/** Archive payloads may include runtime objects. Apply display policy before stringification. */
export function RunEvidenceValue({ label, value }: { label: string; value: unknown }) {
  return <EvidenceValue label={label} value={redactRunEvidence(value)} />;
}
