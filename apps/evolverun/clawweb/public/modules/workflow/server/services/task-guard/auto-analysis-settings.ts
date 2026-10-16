import { createHash } from 'node:crypto';
import YAML from 'yaml';
import type { AppConfigRepository } from '../../repositories/app-config-repository.js';
import type { AutomationPolicyWrite, WorkflowAutomationPolicy } from '../../contracts/workflow-automation.js';
export type AutoAnalysisSetting = WorkflowAutomationPolicy;
export type AutoAnalysisSettings = {
  get(workflowId: string): Promise<AutoAnalysisSetting>;
  set(workflowId: string, enabled: boolean, updatedBy: string): Promise<AutoAnalysisSetting>;
  save(workflowId: string, input: AutomationPolicyWrite, updatedBy: string): Promise<AutoAnalysisSetting>;
  list(): Promise<AutoAnalysisSetting[]>;
  isEnabled(workflowId: string): Promise<boolean>;
};
export type AutoAnalysisConfigRepository = Pick<AppConfigRepository, 'findByKey' | 'compareAndSet' | 'listByPrefix' | 'listWorkflowIds'>;
const PREFIX = 'task_guard_auto_analyze_';
function configKey(id: string) { return PREFIX + createHash('sha256').update(id).digest('hex').slice(0, 32); }
export function createAutoAnalysisSettings(input: { repo: AutoAnalysisConfigRepository | null; environmentDefault?: string }): AutoAnalysisSettings {
  const inherited = new Set(String(input.environmentDefault ?? process.env.TASK_GUARD_AUTO_ANALYZE_FAILED_WORKFLOWS ?? '').split(',').map(s => s.trim()).filter(Boolean));
  return {
    async get(workflowId) {
      const id = workflowId.trim();
      const row = input.repo ? await input.repo.findByKey(configKey(id)) : null;
      const raw = row ? YAML.parse(row.config_yaml) : null;
      if (raw && raw.workflowId !== id) throw new Error('AUTOMATION_POLICY_IDENTITY_MISMATCH');
      const dailyConfigured = raw?.schemaVersion === 'workflow-automation/v1';
      if (dailyConfigured && (typeof raw.autoDraft !== 'boolean' || !/^([01]\d|2[0-3]):[0-5]\d$/.test(raw.dailyTime))) throw new Error('INVALID_AUTOMATION_POLICY');
      if (raw?.minimumImpact !== undefined && !['medium', 'high'].includes(raw.minimumImpact)) throw new Error('INVALID_AUTOMATION_POLICY');
      return { minimumImpact: raw?.minimumImpact ?? 'medium', scopeVersion: raw?.minimumImpact ? 'all-qualified/v1' : 'legacy', workflowId: id, enabled: row ? row.enabled === 1 : inherited.has('*') || inherited.has(id),
        autoDraft: dailyConfigured ? raw.autoDraft : false, dailyTime: dailyConfigured ? raw.dailyTime : '09:00', timezone: 'Asia/Shanghai',
        updatedAtMs: dailyConfigured ? Number(raw.updatedAtMs ?? 0) : 0, version: row?.version ?? 0, dailyConfigured, source: row ? 'database' : inherited.has('*') || inherited.has(id) ? 'environment' : 'default' };
    },
    async save(workflowId, value, actor) {
      if (!input.repo) throw new Error('Task Guard 配置存储不可用');
      const id = workflowId.trim();
      if (value.minimumImpact !== undefined && !['medium', 'high'].includes(value.minimumImpact)) throw new Error('INVALID_AUTOMATION_POLICY');
      if (!id || typeof value.enabled !== 'boolean' || typeof value.autoDraft !== 'boolean'
        || !/^([01]\d|2[0-3]):[0-5]\d$/.test(value.dailyTime) || !Number.isSafeInteger(value.expectedVersion) || value.expectedVersion < 0) throw new Error('INVALID_AUTOMATION_POLICY');
      const prior = await this.get(id);
      const minimumImpact = value.minimumImpact ?? (prior.scopeVersion === 'all-qualified/v1' ? prior.minimumImpact : undefined);
      await input.repo.compareAndSet(configKey(id), { configYaml: JSON.stringify({ schemaVersion: 'workflow-automation/v1',
        workflowId: id, ...(minimumImpact ? { minimumImpact } : {}), updatedAtMs: Date.now(), autoDraft: value.autoDraft, dailyTime: value.dailyTime, timezone: 'Asia/Shanghai' }),
        enabled: value.enabled, expectedVersion: value.expectedVersion, updatedBy: actor });
      return this.get(id);
    },
    async set(id, enabled, actor) {
      const prior = await this.get(id);
      return this.save(id, { enabled, autoDraft: prior.autoDraft, dailyTime: prior.dailyTime, expectedVersion: prior.version }, actor);
    },
    async list() {
      if (!input.repo) return [];
      const policies: AutoAnalysisSetting[] = [];
      for (const row of await input.repo.listByPrefix(PREFIX)) {
        const raw = YAML.parse(row.config_yaml);
        if (typeof raw?.workflowId === 'string') policies.push(await this.get(raw.workflowId));
      }
      const ids = inherited.has('*') ? await input.repo.listWorkflowIds() : [...inherited];
      for (const id of ids) if (!policies.some(policy => policy.workflowId === id)) policies.push(await this.get(id));
      return policies;
    },
    async isEnabled(id) { return (await this.get(id)).enabled; },
  };
}
