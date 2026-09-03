// @asset-migrated: teamclaw 自研资产（任务协作执行 workflow 副屏，路 A 本地注册，进 Open Core）
/**
 * 设计 token —— 反解自 TeamClaw-v3 PRD demo（bundle.js 还原的 12 个色常量）。
 * src/assets 守卫：禁止 antd / 禁止反向 import 业务层；样式只用 styled-components + 这些 token。
 */

/** 12 色常量 */
export const C = {
  page: '#F7F8FA',
  textPrimary: '#1D2129',
  textSecondary: '#86909C',
  textMuted: '#C9CDD4',
  border: '#E5E6EB',
  surface: '#FFFFFF',
  surfaceRaised: '#FBFCFE',
  surfaceAlt: '#F5F7FA',
  primary: '#165DFF',
  primaryBg: '#E8F0FF',
  success: '#00B42A',
  warning: '#FF7D00',
  danger: '#F53F3F',
  review: '#722ED1',
} as const;

/** 任务 7 态药丸配色（y5） */
export interface StatusTone {
  color: string;
  bg: string;
  label: string;
}
export const TASK_STATUS_TONES: Record<string, StatusTone> = {
  DRAFTING: { color: '#86909C', bg: '#F2F3F5', label: '定义中' },
  DEFINED: { color: '#165DFF', bg: '#E8F3FF', label: '待执行' },
  EXECUTING: { color: '#FF7D00', bg: '#FFF7E8', label: '执行中' },
  REVIEWING: { color: '#722ED1', bg: '#F5E8FF', label: '待验收' },
  DONE: { color: '#00B42A', bg: '#E8FFEA', label: '已完成' },
  FAILED: { color: '#F53F3F', bg: '#FFECE8', label: '失败' },
  CANCELLED: { color: '#86909C', bg: '#F2F3F5', label: '已取消' },
};

/** 节点状态 → icon 色 / DAG fill+stroke（F3/B3） */
export interface NodeStatusTone {
  stroke: string;
  fill: string;
}
export const NODE_STATUS_TONES: Record<string, NodeStatusTone> = {
  done: { stroke: C.success, fill: '#E8FFEA' },
  running: { stroke: C.warning, fill: '#FFF7E8' },
  failed: { stroke: C.danger, fill: '#FFECE8' },
  pending: { stroke: C.textMuted, fill: '#F2F3F5' },
  hung: { stroke: C.primary, fill: C.primaryBg },
  cancelled: { stroke: C.border, fill: '#F7F8FA' },
};

/** 任务类型 / 来源 label 映射 */
export const TASK_TYPE_LABELS: Record<string, string> = {
  yaml: 'YAML 编排',
  workflow: '工作流',
  dynamic: '动态任务',
  auto_plan: '自动规划',
};
export const SOURCE_LABELS: Record<string, string> = {
  bot: 'Bot 对话',
  coop_group: '协作群',
  api: 'API',
  user: '用户',
};

/** 产物类型 label */
export const ARTIFACT_TYPE_LABELS: Record<string, string> = {
  document: '文档',
  report: '报告',
  link: '链接',
  file: '文件',
  other: '其他',
};
