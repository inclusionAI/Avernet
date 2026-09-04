/**
 * TaskCards 纯辅助函数（无视觉/无 UI 依赖，供各卡片复用）。
 * normalizeReadyTask 为纯逻辑归一化，亦放此无 UI 处。
 */
import type { TaskCardData, TaskReadyData } from './types';

export function asItems(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : [];
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

/**
 * 从 PanelContentProps.params 读取卡片数据源，兼容 SDK 各层 payload 形态
 * （params.content / params.data / params.payload / params.renderData / params 自身）。
 */
export function readTaskCardData(params: Record<string, unknown>) {
  const candidate = params.content ?? params.data ?? params.payload ?? params;
  const source = isRecord(candidate) && isRecord(candidate.renderData) ? candidate.renderData : candidate;
  return isRecord(source) ? source : {};
}

/**
 * 顶层 task_ready 但未带 task 时用顶层字段补齐；否则沿用 data.task（保持引用，供 execute 携带原对象）。
 */
export function normalizeReadyTask(data: TaskCardData): TaskReadyData {
  return (data.task ?? {
    task_type: 'dynamic',
    goal: data.goal,
    deliverables: data.deliverables,
    acceptance_criteria: data.acceptance_criteria,
    constraints: data.constraints,
    resources: data.resources,
  }) as TaskReadyData;
}
