/** COMMUNITY STUB: flow-control-api-repository.ts is internal-only. Corp extensions provide real implementation. */
import { SqliteFlowControlRepository } from "../../flow-control/repository.js";

export function isFlowDenied(..._args: any[]): boolean { return false; }
export function purgeDenyList(..._args: any[]): void {}

export class FlowControlApiRepository extends SqliteFlowControlRepository {
  constructor(..._args: any[]) {
    super(null as any);
  }
}
