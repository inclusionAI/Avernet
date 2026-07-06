export type MessageRole = 'user' | 'assistant' | 'system';

export interface Task {
  id: string;
  title: string;
  status: 'pending' | 'in-progress' | 'completed';
}

export interface ThinkingProcess {
  thought: string;
  tasks: Task[];
  isExpanded: boolean;
}

export interface Message {
  id: string;
  role: MessageRole;
  content: string;
  timestamp: number;
  thinking?: ThinkingProcess;
  attachments?: Attachment[];
  options?: string[]; // For Human-in-the-loop branch selection
}

export interface Attachment {
  id: string;
  name: string;
  type: 'image' | 'video' | 'audio' | 'document' | 'link';
  url: string;
  size?: string;
}

export interface Conversation {
  id: string;
  title: string;
  lastMessage: string;
  updatedAt: number;
  messages: Message[];
  isPinned?: boolean;
  unread?: boolean;
  message_count?: number;
  gmt_modified?: number;
  last_message?: any;
}

export interface Skill {
  id: string;
  name: string;
  description: string;
  icon: string;
  category: string;
  category_path?: string;
  domain?: string;
  tags?: string[];
  inputDesc?: string;
  outputDesc?: string;
  createdAt: number;
  updatedAt: number;
  git_path?: string;
}

export interface PublicSkill {
  id: string;
  name: string;
  description: string;
  icon: string;
  category: string;
  full_path: string;
  link_name: string;
  version: string;
}

export interface SkillSet {
  id: string;
  name: string;
  type: 'default' | 'custom';
  skills: any[]; // Skill IDs
  is_default?: boolean; // 是否为默认技能集
  is_active?: boolean; // 是否已激活
}

export interface Resource {
  id: string;
  name: string;
  type: 'file' | 'link' | 'path';
  url?: string;
  path?: string;
  size?: string;
  updatedAt: number;
  parentId?: string; // For folder structure
  extension?: string;
  is_directory?: boolean;
  child_count?: number;
}

export interface ResourceFolder {
  id: string;
  name: string;
  parentId?: string;
  path?: string;
  childCount?: number;
}

/** 文件系统条目（新版 path-based API） */
export interface FileSystemItem {
  path: string;
  name: string;
  is_dir: boolean;
  readonly: boolean;
  size: number | null;
  size_human: string | null;
  modified_at: string | null;
  /** 文件/文件夹在磁盘上的绝对路径，用于"复制路径"功能 */
  absolute_path?: string;
}

/** 文件编辑模式 */
export type FileEditMode = 'codemirror' | 'markdown' | 'none';

export interface ModelCapabilities {
  context_window?: number;
  max_output_tokens?: number;
  vision?: boolean;
  function_calling?: boolean;
  reasoning?: boolean;
  streaming?: boolean;
  json_mode?: boolean;
}

export interface ModelPricing {
  input_price?: number;
  output_price?: number;
  cache_read_price?: number;
  cache_write_price?: number;
}

export interface Model {
  id: string;
  provider_id: string;
  provider: string;
  name: string;
  display_name: string;
  description?: string;
  enterprise_enabled?: boolean;
  enterprise_default?: boolean;
  capabilities?: ModelCapabilities;
  pricing?: ModelPricing;
}

export interface ModelConfig {
  modelId: string;
  systemPrompt: string;
  temperature: number;
  topP: number;
  maxTokens: number;
}
