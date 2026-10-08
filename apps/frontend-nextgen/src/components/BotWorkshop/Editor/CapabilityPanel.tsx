import type { BotCapabilitySet, BotEditorMcp, BotEditorSkill } from '@/domain/botEditor';
import { CapabilitySetManager, type CapabilitySetManagerProps } from './CapabilitySetManager';
import { LocalSkillsPanel } from './LocalSkillsPanel';

interface CapabilityPanelProps
  extends Omit<
    CapabilitySetManagerProps,
    'sets' | 'marketSkills' | 'skillCenterSkills' | 'workshopSkills' | 'marketMcps'
  > {
  desktop?: boolean;
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
      <CapabilitySetManager
        {...actions}
        sets={skillSets}
        mySkills={mySkills}
        marketSkills={marketSkills}
        skillCenterSkills={skillCenterSkills}
        workshopSkills={workshopSkills}
        marketMcps={availableMcps}
      />
      {desktop && onLocalToggle && onLocalDelete && onLocalUpload ? (
        <LocalSkillsPanel
          skills={mySkills}
          editable={actions.editable}
          onToggle={onLocalToggle}
          onDelete={onLocalDelete}
          onUpload={onLocalUpload}
        />
      ) : null}
    </>
  );
}
