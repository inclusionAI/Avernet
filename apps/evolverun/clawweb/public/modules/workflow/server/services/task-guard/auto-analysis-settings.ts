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
function notificationKey(id: string) { return 'workflow_repair_notify_' + createHash('sha256').update(id).digest('hex').slice(0, 32); }
function recipients(value: unknown): string[] {
  if (!Array.isArray(value) || value.length > 20 || value.some(id => typeof id !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9_.@:-]{0,127}$/.test(id.trim()))) throw new Error('INVALID_NOTIFICATION_RECIPIENTS');
  return [...new Set(value.map(id => id.trim()))];
}
export function createAutoAnalysisSettings(input: { repo: AutoAnalysisConfigRepository | null; environmentDefault?: string }): AutoAnalysisSettings {
  const inherited = new Set(String(input.environmentDefault ?? process.env.TASK_GUARD_AUTO_ANALYZE_FAILED_WORKFLOWS ?? '').split(',').map(s => s.trim()).filter(Boolean));
  return {
    async get(workflowId) {
      const id = workflowId.trim();
      const row = input.repo ? await input.repo.findByKey(configKey(id)) : null;
      const notice = input.repo ? await input.repo.findByKey(notificationKey(id)) : null;
      const noticeConfig = notice ? YAML.parse(notice.config_yaml) : null;
      if (noticeConfig && noticeConfig.workflowId !== id) throw new Error('AUTOMATION_POLICY_IDENTITY_MISMATCH');
      const raw = row ? YAML.parse(row.config_yaml) : null;
      if (raw && raw.workflowId !== id) throw new Error('AUTOMATION_POLICY_IDENTITY_MISMATCH');
      const dailyConfigured = raw?.schemaVersion === 'workflow-automation/v1' && raw.dailyConfigured !== false;
      if (raw?.schemaVersion === 'workflow-automation/v1' && (typeof raw.autoDraft !== 'boolean' || !/^([01]\d|2[0-3]):[0-5]\d$/.test(raw.dailyTime))) throw new Error('INVALID_AUTOMATION_POLICY');
      if (raw?.minimumImpact !== undefined && !['medium', 'high'].includes(raw.minimumImpact)) throw new Error('INVALID_AUTOMATION_POLICY');
      return { notificationRecipients: recipients(noticeConfig?.recipients ?? []), notificationVersion: notice?.version ?? 0, minimumImpact: raw?.minimumImpact ?? 'medium', scopeVersion: raw?.minimumImpact ? 'all-qualified/v1' : 'legacy', workflowId: id, enabled: row ? row.enabled === 1 : inherited.has('*') || inherited.has(id),
        autoDraft: raw?.schemaVersion === 'workflow-automation/v1' ? raw.autoDraft : false, dailyTime: raw?.schemaVersion === 'workflow-automation/v1' ? raw.dailyTime : '09:00', timezone: 'Asia/Shanghai',
        updatedAtMs: Number(raw?.updatedAtMs ?? 0), version: row?.version ?? 0, dailyConfigured, source: row ? 'database' : inherited.has('*') || inherited.has(id) ? 'environment' : 'default' };
    },
    async save(workflowId, value, actor) {
      if (!input.repo) throw new Error('Task Guard 配置存储不可用');
      const id = workflowId.trim();
      if (value.minimumImpact !== undefined && !['medium', 'high'].includes(value.minimumImpact)) throw new Error('INVALID_AUTOMATION_POLICY');
      if (value.dailyConfigured !== undefined && typeof value.dailyConfigured !== 'boolean') throw new Error('INVALID_AUTOMATION_POLICY');
      if (!id || typeof value.enabled !== 'boolean' || typeof value.autoDraft !== 'boolean'
        || !/^([01]\d|2[0-3]):[0-5]\d$/.test(value.dailyTime) || !Number.isSafeInteger(value.expectedVersion) || value.expectedVersion < 0) throw new Error('INVALID_AUTOMATION_POLICY');
      const prior = await this.get(id);
      if (value.notificationRecipients !== undefined) {
        const notificationRecipients = recipients(value.notificationRecipients);
        if (!Number.isSafeInteger(value.expectedNotificationVersion) || value.expectedNotificationVersion! < 0) throw new Error('INVALID_NOTIFICATION_RECIPIENTS');
        if (value.expectedVersion !== prior.version) throw new Error('SETTINGS_CONFLICT');
        // Saving recipients cannot silently change scheduling or invalidate in-flight automatic requests.
        if (value.enabled !== prior.enabled || value.autoDraft !== prior.autoDraft || value.dailyTime !== prior.dailyTime
          || value.dailyConfigured !== undefined && value.dailyConfigured !== prior.dailyConfigured
          || value.minimumImpact !== undefined && value.minimumImpact !== prior.minimumImpact) throw new Error('INVALID_AUTOMATION_POLICY');
        await input.repo.compareAndSet(notificationKey(id), { configYaml: JSON.stringify({ workflowId: id, recipients: notificationRecipients }),
          enabled: true, expectedVersion: value.expectedNotificationVersion!, updatedBy: actor });
        return this.get(id);
      }
      const minimumImpact = value.minimumImpact ?? (prior.scopeVersion === 'all-qualified/v1' ? prior.minimumImpact : undefined);
      await input.repo.compareAndSet(configKey(id), { configYaml: JSON.stringify({ schemaVersion: 'workflow-automation/v1',
        workflowId: id, dailyConfigured: value.dailyConfigured ?? true, ...(minimumImpact ? { minimumImpact } : {}), updatedAtMs: Date.now(), autoDraft: value.autoDraft, dailyTime: value.dailyTime, timezone: 'Asia/Shanghai' }),
        enabled: value.enabled, expectedVersion: value.expectedVersion, updatedBy: actor });
      return this.get(id);
    },
    async set(id, enabled, actor) {
      const prior = await this.get(id);
      return this.save(id, { enabled, autoDraft: prior.autoDraft, dailyTime: prior.dailyTime, dailyConfigured: prior.dailyConfigured, expectedVersion: prior.version }, actor);
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
