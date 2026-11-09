import type { BotCapabilitySet, BotEditorMcp, BotEditorSkill } from '@/domain/botEditor';
import type { PendingSkillSetToggle } from '@/hooks/useSkillSetActivation';

export interface CapabilitySetManagerProps {
  botId?: string;
  /** 当前登录用户；未解析身份时仅保留本次编辑内的选择。 */
  authenticatedUserId?: string;
  ownerId?: string;
  sets: BotCapabilitySet[];
  mySkills: BotEditorSkill[];
  marketSkills: BotEditorSkill[];
  skillCenterSkills: BotEditorSkill[];
  workshopSkills: BotEditorSkill[];
  marketMcps: BotEditorMcp[];
  editable: boolean;
  /** 调试对话同屏时为 true：成员列表退回单列；调试区收起后恢复多列断点 */
  compact?: boolean;
  onCreate: (name: string) => Promise<void>;
  onDelete: (id: string) => Promise<void>;
  onActive: (set: BotCapabilitySet, active: boolean) => Promise<void>;
  pendingSkillSetToggle?: PendingSkillSetToggle;
  onSkill: (setId: string, skillId: string, active: boolean) => Promise<void>;
  onSkillCenterReferences: (setId: string, skillCodes: string[]) => Promise<void>;
  onUploadSkillFolder: (files: File[]) => Promise<BotEditorSkill>;
  onLocalToggle?: (skill: BotEditorSkill) => Promise<void>;
  onLocalDelete?: (id: string) => Promise<void>;
  onLocalUpload?: (file: File) => Promise<void>;
  onMcp: (setId: string, serverCode: string, active: boolean) => Promise<void>;
  onLoadCandidates: (
    kind: 'skill' | 'mcp',
    mcpQuery?: { source: 'internal' | 'open-platform'; keyword?: string },
  ) => Promise<void>;
  candidatesLoading?: boolean;
  mcpCallTypes?: Record<string, 'caller' | 'owner'>;
  callerContextEditable?: boolean;
  mcpIdentityDisabledReason?: string;
  updatingCallType?: string;
  onMcpCallType?: (serverCode: string, callType: 'caller' | 'owner') => Promise<void>;
}

export type CapabilityPicker = { set: BotCapabilitySet; kind: 'skill' | 'mcp' };
