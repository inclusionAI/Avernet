import type { RequestIdentity } from "./request-identity.js";

export type BotSkillSummary = {
  skillId: string;
  displayName: string;
  description?: string | null;
};

/** The host owns live Local Skills and Bot metadata. Package snapshots are frozen;
 * display metadata is live and never changes the registering user's permissions.
 * Metadata fields/method remain optional for older injected providers (unknown => null).
 */
export interface BotSkillGateway {
  /** Live display metadata only; never substitutes for the registering user's authorization. */
  getBotMetadata?(input: {
    botId: string;
    identity: RequestIdentity;
  }): Promise<{ ownerId: string | null }>;
  listLocalSkills(input: {
    botId: string;
    identity: RequestIdentity;
  }): Promise<BotSkillSummary[]>;
  exportLocalSkill(input: {
    botId: string;
    skillId: string;
    ownerUserId: string;
    identity: RequestIdentity;
  }): Promise<{ packageBytes: Buffer; sha256: string; displayName: string; description?: string | null }>;
  /** A pre-write rejection may set writeNotStarted=true. Never set it after
   * starting a mutation or when the external outcome is unknown. */
  replaceLocalSkill(input: {
    botId: string;
    skillId: string;
    ownerUserId: string;
    /** Read-before-upload check; this port does not promise atomic provider CAS. */
    expectedSha256: string;
    packageBytes: Buffer;
    identity: RequestIdentity;
  }): Promise<{ sha256: string }>;
};
