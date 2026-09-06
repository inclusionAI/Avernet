import type { InsightAutoRepairRepository } from "../repositories/insight-auto-repair-repository.js";
import type { InsightImprovementRepository } from "../repositories/insight-improvement-repository.js";
import type {
  GovernanceRuleProvider,
} from "../services/insight/governance-rule-provider.js";
import type {
  CreateInsightTaskInput,
  InsightTaskCreationResult,
} from "../services/evolve/insight-task-service.js";
import type { FrozenEvidenceReader } from "../services/evolve/task-source-service.js";

/**
 * Process-local composition port supplied by the ClawWeb/ClawInsight owner.
 *
 * This deliberately keeps the current domain objects intact: R5A changes only
 * their construction/ownership boundary and does not rewrite business logic.
 */
export type ClawInsightInternalApi = {
  improvementRepository: InsightImprovementRepository;
  autoRepairRepository: InsightAutoRepairRepository | null;
  governanceRuleProvider: GovernanceRuleProvider | null;
  readFrozenEvidence?: FrozenEvidenceReader;
};

export type ClawEvolveInternalApi = {
  createInsightTask(input: CreateInsightTaskInput): Promise<InsightTaskCreationResult>;
};
