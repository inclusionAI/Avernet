import type { SessionEvidence } from "../contracts.js";

export type EvidenceReadOptions = {
  versionId?: string | null;
  expectedEtag?: string | null;
};

export interface EvidenceProvider {
  readEvidence(payloadRef: string, options?: EvidenceReadOptions): Promise<SessionEvidence>;
}
