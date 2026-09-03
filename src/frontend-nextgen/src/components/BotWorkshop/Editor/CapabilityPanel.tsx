import type { BotCapabilitySet, BotEditorMcp, BotEditorSkill } from '@/domain/botEditor';
import { CapabilitySetManager, type CapabilitySetManagerProps } from './CapabilitySetManager';

interface CapabilityPanelProps
  extends Omit<
    CapabilitySetManagerProps,
    'sets' | 'marketSkills' | 'skillCenterSkills' | 'workshopSkills' | 'marketMcps'
  > {
  skillSets: BotCapabilitySet[];
  mySkills: BotEditorSkill[];
  marketSkills: BotEditorSkill[];
  skillCenterSkills: BotEditorSkill[];
  workshopSkills: BotEditorSkill[];
  availableMcps: BotEditorMcp[];
}

export function CapabilityPanel({
  skillSets,
  mySkills,
  marketSkills,
  skillCenterSkills,
  workshopSkills,
  availableMcps,
  ...actions
}: CapabilityPanelProps) {
  return (
    <CapabilitySetManager
      {...actions}
      sets={skillSets}
      mySkills={mySkills}
      marketSkills={marketSkills}
      skillCenterSkills={skillCenterSkills}
      workshopSkills={workshopSkills}
      marketMcps={availableMcps}
    />
  );
}
