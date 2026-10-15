/**
 * TaskCards 纯辅助函数（无视觉/无 UI 依赖，供各卡片复用）。
 * normalizeReadyTask 为纯逻辑归一化，亦放此无 UI 处。
 */
import type { TaskCardData, TaskReadyData } from './types';

export function asItems(value: unknown): string[] {
  if (typeof value === 'string') {
    const item = value.trim();
    return item ? [item] : [];
  }
  if (!Array.isArray(value)) return [];
  return value
    .filter((item): item is string => typeof item === 'string')
    .map((item) => item.trim())
    .filter(Boolean);
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

function decodeCardData(value: unknown): unknown {
  if (typeof value !== 'string') return value;

  try {
    return JSON.parse(value);
  } catch {
    return value;
  }
}

/**
 * 从 PanelContentProps.params 读取卡片数据源，兼容 SDK 各层 payload 形态
 * （params.content / params.data / params.payload / params.renderData / params 自身）。
 * AixUI 标签 body 在不同渲染链路中可能已解析为对象，也可能仍是 JSON 字符串。
 */
export function readTaskCardData(params: Record<string, unknown>) {
  const candidate = decodeCardData(params.content ?? params.data ?? params.payload ?? params);
  const source = isRecord(candidate) && isRecord(candidate.renderData) ? candidate.renderData : candidate;
  return isRecord(source) ? source : {};
}

/**
 * 顶层 task_ready 但未带 task（或 task 却缺可见字段）时，用顶层字段补齐；
 * 末端再统一归一化列表字段，防止旧版卡片把列表写成字符串后调用
 * Array.prototype.join 崩溃。
 */
export function normalizeReadyTask(data: TaskCardData): TaskReadyData {
  const task = data.task;
  const hasCompleteVisibleFields = Boolean(
    task &&
      typeof task.goal === 'string' &&
      Array.isArray(task.deliverables) &&
      Array.isArray(task.acceptance_criteria) &&
      Array.isArray(task.constraints),
  );

  const base: TaskReadyData = task && hasCompleteVisibleFields
    ? task
    : {
        task_type: task?.task_type ?? 'dynamic',
        workflow_id: task?.workflow_id,
        goal: task?.goal ?? data.goal,
        deliverables: task?.deliverables ?? data.deliverables,
        acceptance_criteria: task?.acceptance_criteria ?? data.acceptance_criteria,
        constraints: task?.constraints ?? data.constraints,
        resources: task?.resources ?? data.resources,
      };

  return {
    ...base,
    ...(data.hidden_flags ? { hidden_flags: data.hidden_flags } : {}),
    deliverables: asItems(base.deliverables),
    acceptance_criteria: asItems(base.acceptance_criteria),
    constraints: asItems(base.constraints),
    resources: asItems(base.resources),
  };
}
