import type { AisExecutor } from "../../contracts/ais-executor.js";
import type { EvolveRepository, EvolveTaskRow } from "../../repositories/evolve-repository.js";
import type { ObjectStore } from "../object-storage/oss-object-store.js";

export type AisArtifactSpec = { objectKey: string; contentType?: string };

export type AisTaskDefinition<TConfig> = {
  taskTypes: readonly string[];
  snapshotId: number | ((config: TConfig) => number);
  artifactTransport?: "signed_put" | "none";
  dispatchMetadata?(config: TConfig): Record<string, unknown>;
  buildGlobalParams(config: TConfig, uploadArtifacts: Record<string, unknown>): Record<string, string>;
};

/** Platform-neutral AIS dispatch for ClawEvolve tasks. */
export class AisTaskRunner<TConfig extends { artifacts: Record<string, AisArtifactSpec> }> {
  constructor(
    private readonly repo: EvolveRepository,
    private readonly store: Pick<ObjectStore, "createSignedUrl">,
    private readonly ais: Pick<AisExecutor, "execute" | "jobUrl">,
    private readonly definition: AisTaskDefinition<TConfig>,
  ) {}

  supports(task: EvolveTaskRow): boolean {
    return this.definition.taskTypes.includes(task.task_type);
  }

  config(task: EvolveTaskRow): TConfig {
    return JSON.parse(task.config_json) as TConfig;
  }

  async prepare(config: TConfig): Promise<Record<string, string>> {
    const uploads: Record<string, unknown> = {};
    if ((this.definition.artifactTransport ?? "signed_put") === "signed_put") {
      for (const [name, item] of Object.entries(config.artifacts)) {
        const contentType = item.contentType;
        if (contentType != null && (typeof contentType !== "string"
          || !contentType.trim() || contentType.length > 128 || /[\r\n\0]/u.test(contentType))) {
          throw new Error("AIS artifact contentType is malformed");
        }
        const putUrl = contentType == null
          ? await this.store.createSignedUrl(item.objectKey, "PUT", 86_400)
          : await this.store.createSignedUrl(
            item.objectKey,
            "PUT",
            86_400,
            { "Content-Type": contentType },
          );
        uploads[name] = { ...item, putUrl };
      }
    }
    return this.definition.buildGlobalParams(config, uploads);
  }

  async dispatch(task: EvolveTaskRow, stepId: string, userId: string): Promise<string> {
    const config = this.config(task);
    const snapshotId = typeof this.definition.snapshotId === "function"
      ? this.definition.snapshotId(config)
      : this.definition.snapshotId;
    if (!Number.isSafeInteger(snapshotId) || snapshotId <= 0) {
      throw new Error("AIS snapshotId must be a positive safe integer");
    }
    const jobId = await this.ais.execute(userId, await this.prepare(config), snapshotId);
    const jobUrl = this.ais.jobUrl?.(jobId);
    await this.repo.markExternalDispatched(stepId, jobId, {
      jobId,
      ...(jobUrl ? { jobUrl } : {}),
      snapshotId,
      submittedBy: userId,
      ...(this.definition.dispatchMetadata?.(config) ?? {}),
    });
    return jobId;
  }
}
