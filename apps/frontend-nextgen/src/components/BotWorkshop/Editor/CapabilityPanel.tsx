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
  currentMcps?: BotEditorMcp[];
  loadError?: string;
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
  currentMcps = [],
  loadError,
  ...actions
}: CapabilityPanelProps) {
  const listedSkills = new Set(skillSets.flatMap((set) => set.skills.map((skill) => skill.id)));
  const listedMcps = new Set(skillSets.flatMap((set) => set.mcps.map((mcp) => mcp.serverCode)));
  const ungroupedSkills = mySkills.filter((skill) => !listedSkills.has(skill.id));
  const ungroupedMcps = currentMcps.filter((mcp) => !listedMcps.has(mcp.serverCode));
  // 无分组外事实且无加载错误时不渲染整块，避免空态文案常驻能力面板顶部
  const hasFacts = Boolean(loadError) || ungroupedSkills.length > 0 || ungroupedMcps.length > 0;
  return (
    <>
      {hasFacts && (
      <section aria-label="Bot 当前能力" className="space-y-3 border-b border-border bg-card px-8 py-5 lg:px-10">
        <h2 className="text-base font-semibold">Bot 当前能力</h2>
        <p className="text-sm text-muted-foreground">下方能力集以外的本地 Skill 与 MCP 配置</p>
        {loadError && <p role="alert" className="text-sm text-destructive">{loadError}</p>}
        {ungroupedSkills.map((skill) => (
          <div key={skill.id} className="flex items-center gap-3 text-sm">
            <span className="text-muted-foreground">本地 Skill</span>
            <span className="min-w-0 flex-1 break-words">{skill.name}</span>
            <span className="text-muted-foreground">{skill.active ? '已启用' : '未启用'}</span>
          </div>
        ))}
        {ungroupedMcps.map((mcp) => (
          <div key={mcp.serverCode} className="flex items-center gap-3 text-sm">
            <span className="text-muted-foreground">MCP</span>
            <span className="min-w-0 flex-1 break-words">{mcp.name}</span>
            <span className="text-muted-foreground">{mcp.active ? '已添加' : '未添加'}</span>
          </div>
        ))}
      </section>
      )}
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
