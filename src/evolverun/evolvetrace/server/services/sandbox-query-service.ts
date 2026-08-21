/**
 * Stub SandboxQueryService for Evolvetrace.
 * The original clawweb implementation queries ARCA and BaaS Meta tables.
 */

export type SandboxQueryResult = {
  arca: string[];
  baas: string[];
};

export class SandboxQueryService {
  async query(_botId: string, _entityId: number): Promise<SandboxQueryResult> {
    return { arca: [], baas: [] };
  }
}
