import type { BotHealthDimensionKey, BotHealthDimensionLevel } from '@/domain/botHealthCheck';

export const MAIN_TAB_DIMENSION = 'dimension' as const;
export type HealthCheckMainTab = typeof MAIN_TAB_DIMENSION;

export interface DimensionInfo {
  scanDim: string;
  key: BotHealthDimensionLevel;
  dimensionKey: BotHealthDimensionKey;
  name: string;
  description: string;
  angle: number;
}

export const DIMENSIONS_INFO: DimensionInfo[] = [
  {
    scanDim: 'full:L1',
    key: 'L1',
    dimensionKey: 'configuration',
    name: '配置健康度',
    description: 'Bot 是否有基础护栏',
    angle: -Math.PI / 2,
  },
  {
    scanDim: 'full:L2',
    key: 'L2',
    dimensionKey: 'taskUnderstanding',
    name: '任务理解力',
    description: 'Bot 是否听懂任务',
    angle: -Math.PI / 2 + (2 * Math.PI) / 6,
  },
  {
    scanDim: 'full:L3',
    key: 'L3',
    dimensionKey: 'planningExecution',
    name: '规划执行力',
    description: 'Bot 能否拆解和推进任务',
    angle: -Math.PI / 2 + (4 * Math.PI) / 6,
  },
  {
    scanDim: 'full:L4',
    key: 'L4',
    dimensionKey: 'capabilityInvocation',
    name: '能力调用力',
    description: 'Bot 调用能力',
    angle: -Math.PI / 2 + (6 * Math.PI) / 6,
  },
  {
    scanDim: 'full:L5',
    key: 'L5',
    dimensionKey: 'contextLearning',
    name: '上下文学习力',
    description: 'Bot 能被持续养育',
    angle: -Math.PI / 2 + (8 * Math.PI) / 6,
  },
  {
    scanDim: 'full:L6',
    key: 'L6',
    dimensionKey: 'taskDelivery',
    name: '任务交付力',
    description: '最终是否完成任务、产出可用结果',
    angle: -Math.PI / 2 + (10 * Math.PI) / 6,
  },
];

export const DIM_NAME_MAPPING: Record<string, string> = Object.fromEntries(
  DIMENSIONS_INFO.map((dim) => [dim.scanDim, dim.name]),
);

export const DIM_KEY_MAPPING: Record<string, BotHealthDimensionLevel> = Object.fromEntries(
  DIMENSIONS_INFO.map((dim) => [dim.scanDim, dim.key]),
);

export const KEY_TO_SCAN_DIM_MAPPING: Record<BotHealthDimensionLevel, string> = {
  L1: 'full:L1',
  L2: 'full:L2',
  L3: 'full:L3',
  L4: 'full:L4',
  L5: 'full:L5',
  L6: 'full:L6',
};

export type ResultKey = 'pass' | 'warning' | 'fail' | 'error' | 'pending' | 'running';

export const RESULT_STYLES: Record<
  ResultKey,
  { icon: string; tone: 'success' | 'warning' | 'error' | 'neutral' | 'primary'; label: string }
> = {
  pass: { icon: '✓', tone: 'success', label: '通过' },
  warning: { icon: '⚠', tone: 'warning', label: '警告' },
  fail: { icon: '✕', tone: 'error', label: '错误' },
  error: { icon: '!', tone: 'neutral', label: '检测失败' },
  pending: { icon: '○', tone: 'neutral', label: '待检测' },
  running: { icon: '⟳', tone: 'primary', label: '检测中' },
};

export const DIM_STATUS_STYLES: Record<
  string,
  { label: string; tone: 'primary' | 'warning' | 'success' | 'error' | 'neutral' }
> = {
  passed: { label: '已完成', tone: 'success' },
  scanning: { label: '检测中', tone: 'primary' },
  patching: { label: '生成建议中', tone: 'warning' },
  completed: { label: '已完成', tone: 'success' },
  failed: { label: '检测失败', tone: 'error' },
  warning: { label: '需关注', tone: 'warning' },
  error: { label: '异常', tone: 'error' },
  unknown: { label: '未知', tone: 'neutral' },
};

export const REPORT_TYPE_LABELS: Record<string, string> = {
  normal: '默认',
  daily: '日报',
  weekly: '周报',
};
