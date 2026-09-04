import type { BotEditorMcp, BotEditorSkill } from '@/domain/botEditor';
import { botEditorController, type SpaceSkillDto } from '@/services/backendApi/bots/botEditorController';
import { dataOr } from './botEditorMappers';

export interface CapabilityCandidateOptions {
  skillSources: Array<'mine' | 'market' | 'workshop'>;
  mcp: boolean;
}

async function listAllConsumableSpaceSkills(spaceId: string): Promise<SpaceSkillDto[]> {
  const pageSize = 100;
  const items: SpaceSkillDto[] = [];
  for (let page = 1; ; page += 1) {
    const response = await botEditorController.listConsumableSpaceSkills(spaceId, page, pageSize);
    const pageItems = response.data?.items ?? [];
    items.push(...pageItems);
    const total = response.data?.total;
    if (!pageItems.length || pageItems.length < pageSize || (total !== undefined && items.length >= total)) break;
  }
  return items;
}

export async function loadCapabilityCandidates(
  botId: string,
  spaceId?: string,
  options: CapabilityCandidateOptions = { skillSources: ['market', 'workshop', 'mine'], mcp: true },
) {
  const marketEnabled = options.skillSources.includes('market');
  const workshopEnabled = options.skillSources.includes('workshop');
  const [boundMcps, mcpServers, repositorySkills, spaceSkills] = await Promise.all([
    options.mcp ? botEditorController.listBotMcps(botId) : Promise.resolve({ data: [] }),
    options.mcp ? botEditorController.listMcpServers() : Promise.resolve({ data: { items: [] } }),
    marketEnabled ? botEditorController.listRepositorySkills() : Promise.resolve({ data: { items: [] } }),
    workshopEnabled && spaceId ? listAllConsumableSpaceSkills(spaceId) : Promise.resolve([]),
  ]);
  return {
    availableMcps: dataOr(mcpServers.data?.items, []).map(
      (item): BotEditorMcp => ({
        serverCode: item.server_code,
        name: item.name || item.server_code,
        description: item.description,
        active: Boolean(boundMcps.data?.some((bound) => bound.server_code === item.server_code && bound.active)),
      }),
    ),
    marketSkills: dataOr(repositorySkills.data?.items, []).map(
      (item): BotEditorSkill => ({
        id: String(item.skill_id ?? item.id ?? ''),
        name: item.name,
        description: item.description,
        version: item.latest_published_version ?? item.version,
        source: 'teamclaw-market',
        active: false,
      }),
    ),
    skillCenterSkills: [] as BotEditorSkill[],
    workshopSkills: spaceSkills.map(
      (item): BotEditorSkill => ({
        id: item.skill_id,
        name: item.name,
        description: item.description,
        version: item.latest_published_version?.version ? `V${item.latest_published_version.version}` : undefined,
        source: 'workshop',
        active: false,
      }),
    ),
  };
}
