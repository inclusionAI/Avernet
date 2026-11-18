import type { Experience, Issue, TaskEscortOperationResult, TaskEscortSettings } from '@/domain/taskEscortWorkspace';
import type { TaskEscortWorkspaceStore } from '@/stores/taskEscortWorkspaceStore';

const result = (ok: boolean, message: string): TaskEscortOperationResult => ({ ok, message });
const keys: Array<keyof TaskEscortSettings> = [
  'enabled',
  'autoAnalyze',
  'humanReview',
  'notifications',
  'recurrence',
  'sensitivity',
  'webhook',
];
const differs = (left: TaskEscortSettings, right: TaskEscortSettings) => keys.some((key) => left[key] !== right[key]);

/** Session-only synthetic state machine. No network calls or automatic verification/publication. */
export function createDemoActions(store: TaskEscortWorkspaceStore) {
  let savedSettings = { ...store.getState().snapshot.settings };
  const transition = (
    id: string,
    allowed: Issue['status'][],
    status: Issue['status'],
    message: string,
  ): TaskEscortOperationResult => {
    const state = store.getState().snapshot;
    const issue = state.issues.find((item) => item.id === id);
    if (!issue) return result(false, '未找到该演示问题');
    if (!allowed.includes(issue.status)) {
      return result(
        false,
        status === 'verified' ? '仅已应用建议的问题可以手动验证' : '该问题已应用或已验证，无需重复应用',
      );
    }
    store.getState().setCollections({
      workflows: state.workflows,
      runs: state.runs,
      experiences: state.experiences,
      issues: state.issues.map((item) => (item.id === id ? { ...item, status } : item)),
    });
    return result(true, message);
  };
  return {
    applySuggestion: (id: string) =>
      transition(id, ['open', 'recurring'], 'applied', '建议已应用到演示状态，仍需手动验证后才能沉淀经验'),
    verifyIssue: (id: string) =>
      transition(id, ['applied'], 'verified', '已记录演示手动验证结果，可将此问题沉淀为经验'),
    saveExperience: (id: string): TaskEscortOperationResult => {
      const state = store.getState().snapshot;
      const issue = state.issues.find((item) => item.id === id);
      if (!issue) return result(false, '未找到该演示问题');
      if (issue.status !== 'verified') return result(false, '仅手动验证有效的问题可以沉淀为经验');
      if (state.experiences.some((item) => item.sourceIssueId === id))
        return result(true, '该演示问题已沉淀经验，可前往经验库查看');
      const experience: Experience = {
        id: `exp-${id}`,
        title: issue.title,
        description: `${issue.rootCause} 改进方式：${issue.recommendation}`,
        category: issue.category,
        workflowName: issue.workflowName,
        reuseCount: 0,
        verified: true,
        sourceIssueId: id,
      };
      store.getState().setCollections({
        workflows: state.workflows,
        runs: state.runs,
        issues: state.issues,
        experiences: [experience, ...state.experiences],
      });
      return result(true, '已沉淀为已验证演示经验，可前往经验库查看');
    },
    updateSettings: (patch: Partial<TaskEscortSettings>): TaskEscortOperationResult => {
      if (!patch || typeof patch !== 'object') return result(false, '设置更新格式无效');
      const state = store.getState().snapshot;
      const settings = { ...state.settings };
      for (const key of keys) {
        if (!Object.prototype.hasOwnProperty.call(patch, key)) continue;
        const value = patch[key];
        if (key === 'sensitivity' || key === 'webhook') {
          if (typeof value === 'string') settings[key] = value.trim();
        } else if (typeof value === 'boolean') settings[key] = value;
      }
      if (differs(settings, state.settings)) store.getState().setSettings(settings, differs(settings, savedSettings));
      return result(true, '演示设置已更新，尚未保存');
    },
    saveSettings: (): TaskEscortOperationResult => {
      const state = store.getState().snapshot;
      if (!['balanced', 'high', 'low'].includes(state.settings.sensitivity)) {
        return result(false, '请选择有效的分析敏感度');
      }
      if (state.settings.webhook) {
        try {
          const url = new URL(state.settings.webhook);
          if (!['http:', 'https:'].includes(url.protocol)) throw new Error('invalid protocol');
        } catch {
          return result(false, 'Webhook 请输入有效的 HTTP 或 HTTPS 地址');
        }
      }
      savedSettings = { ...state.settings };
      if (state.settingsDirty) store.getState().setSettings(state.settings, false);
      return result(true, '设置已保存在本次演示中；不会发送网络请求，刷新后恢复初始数据');
    },
    resetSavedSettings: () => {
      savedSettings = { ...store.getState().snapshot.settings };
    },
  };
}
