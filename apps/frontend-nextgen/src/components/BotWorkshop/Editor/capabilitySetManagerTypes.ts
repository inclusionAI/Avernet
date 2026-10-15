import type { BotCapabilitySet, BotEditorMcp, BotEditorSkill } from '@/domain/botEditor';
import type { PendingSkillSetToggle } from '@/hooks/useSkillSetActivation';

export interface CapabilitySetManagerProps {
  botId?: string;
  ownerId?: string;
  sets: BotCapabilitySet[];
  /** 编辑器与调试面板并列时使用单列成员布局。 */
  compact?: boolean;
  mySkills: BotEditorSkill[];
  marketSkills: BotEditorSkill[];
  skillCenterSkills: BotEditorSkill[];
  workshopSkills: BotEditorSkill[];
  marketMcps: BotEditorMcp[];
  editable: boolean;
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
