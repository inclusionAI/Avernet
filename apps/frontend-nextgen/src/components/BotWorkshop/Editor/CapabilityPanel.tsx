import type { BotCapabilitySet, BotEditorMcp, BotEditorSkill } from '@/domain/botEditor';
import { CapabilitySetManager } from './CapabilitySetManager';
import type { CapabilitySetManagerProps } from './capabilitySetManagerTypes';

interface CapabilityPanelProps
  extends Omit<
    CapabilitySetManagerProps,
    'sets' | 'marketSkills' | 'skillCenterSkills' | 'workshopSkills' | 'marketMcps'
  > {
  desktop?: boolean;
  /** 调试面板打开时，编辑区域较窄，成员卡片保持单列。 */
  compact?: boolean;
  onLocalToggle?: (skill: BotEditorSkill) => Promise<void>;
  onLocalDelete?: (id: string) => Promise<void>;
  onLocalUpload?: (file: File) => Promise<void>;
  skillSets: BotCapabilitySet[];
  mySkills: BotEditorSkill[];
  marketSkills: BotEditorSkill[];
  skillCenterSkills: BotEditorSkill[];
  workshopSkills: BotEditorSkill[];
  availableMcps: BotEditorMcp[];
}

export function CapabilityPanel({
  desktop,
  compact,
  onLocalToggle,
  onLocalDelete,
  onLocalUpload,
  skillSets,
  mySkills,
  marketSkills,
  skillCenterSkills,
  workshopSkills,
  availableMcps,
  ...actions
}: CapabilityPanelProps) {
  return (
    <>
      {/* onLocalDelete 不按 desktop 掩蔽：云端 Bot 的「我的 Skill」卡片也支持删除
          （DELETE /bots/{bot_id}/skills/{skill_id}）；本地管理面板(toggle+upload)仍仅本地 Bot */}
      <CapabilitySetManager
        {...actions}
        compact={compact}
        onLocalToggle={desktop ? onLocalToggle : undefined}
        onLocalDelete={onLocalDelete}
        onLocalUpload={desktop ? onLocalUpload : undefined}
        sets={skillSets}
        mySkills={mySkills}
        marketSkills={marketSkills}
        skillCenterSkills={skillCenterSkills}
        workshopSkills={workshopSkills}
        marketMcps={availableMcps}
      />
    </>
  );
}
