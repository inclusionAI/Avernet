// @asset-migrated: teamclaw 自研资产
/**
 * 产物 V2 fixture —— 对齐后端 task-artifact-domain 读侧 Descriptor
 * (apps/backend/specs/2026-10-20-task-artifact-domain §3.4 / §7)。
 * 后端未上线该字段期间,mapper 单测/产物区均以本形状作为 mock 数据源。
 */
import type { TaskArtifactDescriptorDto, TaskArtifactDto } from '../contract';

export const artifactDescriptorWorkspaceFile: TaskArtifactDescriptorDto = {
  artifact_id: 'art-ws-file',
  artifact_name: null, // 缺名回落 content.file_name
  kind: 'file',
  content: {
    storage_type: 'bot_workspace',
    storage_id: 'workspace/report.md',
    file_name: '竞品分析报告.md',
    file_id: null,
    file_type: 'text/markdown',
  },
  attempt: 1,
  creator: 'bot-planner',
  created_at: 1729400000000,
};

export const artifactDescriptorSessionFile: TaskArtifactDescriptorDto = {
  artifact_id: 'art-sess-file',
  artifact_name: '附件材料.pdf',
  kind: 'file',
  content: {
    storage_type: 'session_file',
    storage_id: 'sess-9',
    file_name: '材料.pdf',
    file_id: 'f-1',
    file_type: 'application/pdf',
  },
  attempt: null,
  creator: 'group-902',
  created_at: 1729400100000,
};

export const artifactDescriptorObjectFile: TaskArtifactDescriptorDto = {
  artifact_id: 'art-obj-file',
  artifact_name: null, // 缺名回落 content.file_name
  kind: 'file',
  content: {
    storage_type: 'object_file',
    storage_id: 'task-artifacts/tk_20261027_0001/analysis-report.pdf',
    file_name: 'analysis-report.pdf',
    file_id: null,
    file_type: 'application/pdf',
  },
  attempt: 0,
  creator: 'bot-claw',
  created_at: 1729400150000,
};

export const artifactDescriptorUrlLink: TaskArtifactDescriptorDto = {
  artifact_id: 'art-url',
  artifact_name: '需求说明书',
  kind: 'url_link',
  content: { url: 'https://yuque.example/doc1', type: 'dima需求' },
  attempt: 1,
  creator: 'group-902',
  created_at: 1729400000000,
};

export const artifactDescriptorCode: TaskArtifactDescriptorDto = {
  artifact_id: 'art-code',
  artifact_name: '重构 PR',
  kind: 'code',
  content: {
    code_base_name: 'avernet',
    code_base_address: 'https://git.example/avernet',
    pr_link: 'https://git.example/pr/1024',
    pr_desc: '产物读侧接入',
  },
  attempt: 1,
  creator: 'bot-coder',
  created_at: 1729400200000,
};

export const artifactDescriptorText: TaskArtifactDescriptorDto = {
  artifact_id: 'art-text',
  artifact_name: '阶段结论',
  kind: 'text',
  content: { text: '一段纯文本结论' },
  attempt: 1,
  creator: 'bot-planner',
  created_at: 1729400300000,
};

/** 与 legacy 同 id(验证去重:Descriptor 口径应胜出) */
export const descriptorBothCommon: TaskArtifactDescriptorDto = {
  artifact_id: 'both-common',
  artifact_name: '同名产物(Descriptor 口径)',
  kind: 'url_link',
  content: { url: 'https://yuque.example/both', type: '文档' },
  created_at: 1729400400000,
};

export const descriptorList: TaskArtifactDescriptorDto[] = [
  artifactDescriptorWorkspaceFile,
  artifactDescriptorUrlLink,
  descriptorBothCommon,
];

export const legacyArtifacts: TaskArtifactDto[] = [
  {
    artifact_id: 'both-common', // 与 Descriptor 重叠,应被去重
    node_id: 'n1',
    name: '同名产物(legacy 口径)',
    type: 'link',
    url: 'https://yuque.example/both-legacy',
    created_at: '2026-10-01 10:00:00',
  },
  {
    artifact_id: 'legacy-only', // node_id 缺省,应回落入参 nodeId
    name: '旧的输出文件',
    type: 'file',
    url: null,
    summary: 'legacy-only 产物',
    created_at: '2026-10-01 11:00:00',
  },
];