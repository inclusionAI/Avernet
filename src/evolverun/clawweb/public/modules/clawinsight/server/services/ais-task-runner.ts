import type { EvolveRepository, EvolveTaskRow } from "@avernet/clawevolve/server/repositories/evolve-repository";
import type { MistOssObjectStore } from "./object-storage/oss-object-store.js";
import { AistudioService } from "./aistudio-service.js";

export type AisArtifactSpec = { objectKey: string; contentType?: string };
export type AisTaskDefinition<TConfig> = {
  taskTypes: readonly string[];
  snapshotId: number | ((config: TConfig) => number);
  artifactTransport?: "signed_put" | "none";
  dispatchMetadata?(config: TConfig): Record<string, unknown>;
  buildGlobalParams(config: TConfig, uploadArtifacts: Record<string, unknown>): Record<string, string>;
};

/** Shared executeSnapshot dispatch. Business state is advanced only by executor callbacks. */
export class AisTaskRunner<TConfig extends { artifacts: Record<string, AisArtifactSpec> }> {
  constructor(private readonly repo: EvolveRepository, private readonly store: MistOssObjectStore,
    private readonly ais: AistudioService, private readonly definition: AisTaskDefinition<TConfig>) {}

  supports(task: EvolveTaskRow): boolean { return this.definition.taskTypes.includes(task.task_type); }
  config(task: EvolveTaskRow): TConfig { return JSON.parse(task.config_json) as TConfig; }

  /**
   * Builds the exact one-parameter Snapshot envelope without starting a new job.
   * Repair uses this when the already-running wrapper claims a following CE Step
   * and continues in the same AIStudio container/CC session.
   */
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

  async dispatch(task: EvolveTaskRow, stepId: string, userId: string, configOverride?: TConfig): Promise<string> {
    // configOverride is intentionally process-local. Repair uses it to add the
    // current AIS container's model API key without persisting that secret in
    // ce_tasks.config_json.
    const config = configOverride ?? this.config(task);
    const snapshotId = typeof this.definition.snapshotId === "function"
      ? this.definition.snapshotId(config)
      : this.definition.snapshotId;
    if (!Number.isSafeInteger(snapshotId) || snapshotId <= 0) {
      throw new Error("AIS snapshotId must be a positive safe integer");
    }
    const jobId = await this.ais.execute(userId, await this.prepare(config), snapshotId);
    await this.repo.markExternalDispatched(stepId, jobId, {
      jobId,
      jobUrl: `https://aistudio.alipay.com/project/job/detail/${jobId}`,
      snapshotId,
      submittedBy: userId,
      ...(this.definition.dispatchMetadata?.(config) ?? {}),
    });
    return jobId;
  }
}
