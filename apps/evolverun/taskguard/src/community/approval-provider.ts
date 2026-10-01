/**
 * Community default approval provider.
 * Uses DB-backed approval (human-wait executor).
 */
export function createCommunityApprovalProvider(config: unknown) {
  return {
    provider: "basic" as const,
    async createApproval(_params: unknown) {
      // Basic approval: just wait for manual confirmation via API
      return { approvalId: `basic-${Date.now()}` };
    },
    async checkApproval(_id: string) {
      return { status: "pending" as const };
    },
  };
}
