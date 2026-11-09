/** 前端 Mock 专用模型，不代表社区、任务引擎或 BCS 的服务契约。 */
export const HELP_ROLES = ['需求采集', '架构', '应用', '代码', 'verify', 'pr'] as const;
export type HelpRole = (typeof HELP_ROLES)[number];
export const HELP_SOURCES = ['Dima', 'Task Discover', '手动输入'] as const;
export type HelpSource = (typeof HELP_SOURCES)[number];
export type HelpTaskStatus = 'pending' | 'running' | 'attention' | 'done';
export type HelpStageStatus = 'pending' | 'running' | 'attention' | 'done';
export type HelpCollaborationMode = 'selected' | 'open';

export interface HelpBot {
  id: string;
  name: string;
  description: string;
}
export interface HelpMember {
  botId: string;
  roles: HelpRole[];
}
export interface HelpSourceConfig {
  dima: { projectSpace: string };
  taskDiscover: { botName: string };
}
export type HelpSourceDetails =
  | { kind: 'Dima'; projectSpace: string }
  | { kind: 'Task Discover'; botName: string }
  | { kind: '手动输入' };
export interface HelpBoardDraft {
  collaborationMode: HelpCollaborationMode;
  name: string;
  description: string;
  members: HelpMember[];
  sources: HelpSource[];
  sourceConfig: HelpSourceConfig;
}
export interface HelpBoard extends HelpBoardDraft {
  id: string;
  type: 'AiCoding';
}
export interface HelpTaskDraft {
  title: string;
  description: string;
  source: HelpSource;
  sourceRef: string;
  syncToDima: boolean;
  dimaProjectSpace: string;
}
export interface HelpStage {
  id: string;
  role: HelpRole;
  botNames: string[];
  status: HelpStageStatus;
  instruction: string;
  output: string;
}
export interface HelpActivity {
  id: string;
  at: string;
  message: string;
}
export interface HelpArtifact {
  id: string;
  name: string;
  kind: '需求文档' | '方案文档' | '应用方案' | '代码变更' | '验证报告' | 'PR 摘要';
  content: string;
}
export interface HelpTask extends HelpTaskDraft {
  sourceDetails: HelpSourceDetails;
  id: string;
  boardId: string;
  collaborationMode: HelpCollaborationMode;
  status: HelpTaskStatus;
  createdAt: string;
  stages: HelpStage[];
  activities: HelpActivity[];
  artifacts: HelpArtifact[];
}
