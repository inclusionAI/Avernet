import type { BotCapabilitySet, BotEditorMcp, BotEditorSkill } from '@/domain/botEditor';
import { CapabilitySetManager, type CapabilitySetManagerProps } from './CapabilitySetManager';

interface CapabilityPanelProps
  extends Omit<CapabilitySetManagerProps, 'sets' | 'marketSkills' | 'workshopSkills' | 'marketMcps'> {
  skillSets: BotCapabilitySet[];
  mySkills: BotEditorSkill[];
  marketSkills: BotEditorSkill[];
  workshopSkills: BotEditorSkill[];
  availableMcps: BotEditorMcp[];
}

export function CapabilityPanel({
  skillSets,
  mySkills,
  marketSkills,
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
      workshopSkills={workshopSkills}
      marketMcps={availableMcps}
    />
  );
}
