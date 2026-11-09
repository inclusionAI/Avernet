import type { HelpSource, HelpSourceConfig, HelpSourceDetails, HelpTask } from './types';

export function normalizeHelpSourceConfig(config: HelpSourceConfig): HelpSourceConfig {
  return {
    dima: { projectSpace: config.dima.projectSpace.trim() },
    taskDiscover: { botName: config.taskDiscover.botName.trim() },
  };
}

export function validateHelpSourceConfig(sources: HelpSource[], config: HelpSourceConfig): string | null {
  if (sources.includes('Dima') && !config.dima.projectSpace.trim()) return '请填写 Dima 项目空间';
  if (sources.includes('Task Discover') && !config.taskDiscover.botName.trim()) return '请填写 Task Discover Bot';
  return null;
}

export function createHelpSourceDetails(source: HelpSource, config: HelpSourceConfig): HelpSourceDetails {
  const normalized = normalizeHelpSourceConfig(config);
  if (source === 'Dima') return { kind: source, ...normalized.dima };
  if (source === 'Task Discover') return { kind: source, ...normalized.taskDiscover };
  return { kind: '手动输入' };
}

export function helpSourceContext(details: HelpSourceDetails): string {
  if (details.kind === 'Dima') return `Dima 项目空间：${details.projectSpace}`;
  if (details.kind === 'Task Discover') return `Task Discover Bot：${details.botName}`;
  return '用户手动录入';
}

export function helpDimaSyncSummary(task: HelpTask): string {
  return task.syncToDima ? `Dima 自动同步 · ${task.dimaProjectSpace} · 模拟待同步（未实际同步）` : '未开启自动同步';
}
