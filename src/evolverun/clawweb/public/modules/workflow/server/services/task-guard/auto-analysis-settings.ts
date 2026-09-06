import { createHash } from "node:crypto";
import type { AppConfigRepository } from "../../repositories/app-config-repository.js";

export type AutoAnalysisSetting = {
  workflowId: string;
  enabled: boolean;
  source: "database" | "environment" | "default";
};

export type AutoAnalysisSettings = {
  get(workflowId: string): Promise<AutoAnalysisSetting>;
  set(workflowId: string, enabled: boolean, updatedBy: string): Promise<AutoAnalysisSetting>;
  isEnabled(workflowId: string): Promise<boolean>;
};

function configKey(workflowId: string): string {
  const digest = createHash("sha256").update(workflowId).digest("hex").slice(0, 32);
  return `task_guard_auto_analyze_${digest}`;
}

function enabledWorkflowSet(value: string | undefined): Set<string> {
  return new Set(String(value ?? "").split(",").map((item) => item.trim()).filter(Boolean));
}

export function createAutoAnalysisSettings(input: {
  repo: Pick<AppConfigRepository, "findByKey" | "create" | "update"> | null;
  environmentDefault?: string;
}): AutoAnalysisSettings {
  const environmentEnabled = enabledWorkflowSet(
    input.environmentDefault ?? process.env.TASK_GUARD_AUTO_ANALYZE_FAILED_WORKFLOWS,
  );

  return {
    async get(workflowId) {
      const normalized = workflowId.trim();
      const row = input.repo ? await input.repo.findByKey(configKey(normalized)) : null;
      if (row) {
        return { workflowId: normalized, enabled: row.enabled === 1, source: "database" as const };
      }
      if (environmentEnabled.has("*") || environmentEnabled.has(normalized)) {
        return { workflowId: normalized, enabled: true, source: "environment" as const };
      }
      return { workflowId: normalized, enabled: false, source: "default" as const };
    },

    async set(workflowId, enabled, updatedBy) {
      if (!input.repo) throw new Error("Task Guard 配置存储不可用");
      const normalized = workflowId.trim();
      if (!normalized) throw new Error("workflowId 为必填项");
      const key = configKey(normalized);
      const configYaml = JSON.stringify({ schemaVersion: "task-guard-auto-analysis/v1", workflowId: normalized });
      const existing = await input.repo.findByKey(key);
      if (existing) {
        await input.repo.update(key, { config_yaml: configYaml, enabled: enabled ? 1 : 0, updated_by: updatedBy });
      } else {
        await input.repo.create({
          config_key: key,
          config_yaml: configYaml,
          description: `Task Guard failed-run auto analysis for ${normalized}`,
          updated_by: updatedBy,
        });
        if (!enabled) await input.repo.update(key, { enabled: 0, updated_by: updatedBy });
      }
      return { workflowId: normalized, enabled, source: "database" as const };
    },

    async isEnabled(workflowId) {
      return (await this.get(workflowId)).enabled;
    },
  };
}
