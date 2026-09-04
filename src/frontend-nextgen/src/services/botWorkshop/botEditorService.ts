import type {
  BotCapabilitySet,
  BotEditorMcp,
  BotEditorRoutineInput,
  BotEditorSkill,
  BotEngineConfig,
  BotRenderScreenInput,
} from '@/domain/botEditor';
import { botEditorController, type SpaceSkillDto } from '@/services/backendApi/bots/botEditorController';
import { clearBotCdnConfig, storeBotCdnConfigs } from '@/services/bcs/libraryCdnInjector';
import {
  dataOr,
  mapPublication,
  mapResource,
  mapRoutine,
  mapScreen,
  mapSkill,
  toRoutineWrite,
} from './botEditorMappers';

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

export const botEditorService = {
  async getCallerContext(botId: string) {
    const response = await botEditorController.getCallerContext(botId);
    return {
      editable: response.data?.editable ?? false,
      mcpCallTypes: response.data?.mcp_call_types ?? {},
      cliCallTypes: response.data?.cli_call_types ?? {},
    };
  },
  async updateMcpCallType(botId: string, serverCode: string, callType: 'caller' | 'owner') {
    const response = await botEditorController.updateMcpCallType(botId, serverCode, callType);
    if (!response.data) throw new Error('修改调用身份后未返回结果');
    return response.data.call_type;
  },
  async registerRenderScreenLibraries(botId: string) {
    if (!botId) return 0;
    try {
      const response = await botEditorController.listRenderScreens(botId);
      const screens = response.data?.items ?? [];
      storeBotCdnConfigs(botId, screens);
      return screens.length;
    } catch {
      clearBotCdnConfig(botId);
      return 0;
    }
  },
  async load(botId: string, serviceBot = false) {
    const [skills, skillSetResources, resources, screens, routines, engineConfig, engineStatus, approval] =
      await Promise.allSettled([
        botEditorController.listSkills(botId),
        botEditorController.listSkillSetResources(botId),
        botEditorService.listResources(botId),
        botEditorController.listRenderScreens(botId),
        botEditorController.listRoutines(botId),
        botEditorController.getEngineConfig(botId),
        botEditorController.getEngineStatus(botId),
        serviceBot
          ? botEditorController.getApprovalConfig(botId)
          : Promise.resolve({ data: { should_approval: false } }),
      ]);
    const sets: BotCapabilitySet[] = [];
    let skillSetDetailErrors = 0;
    if (skillSetResources.status === 'fulfilled') {
      const details = await Promise.all(
        (skillSetResources.value.data ?? []).map(async (set) => {
          const setSkills = await Promise.allSettled([botEditorController.listSkillSetSkills(botId, set.id)]).then(
            ([result]) => result,
          );
          skillSetDetailErrors += Number(setSkills.status === 'rejected');
          return {
            id: set.id,
            name: set.name,
            description: set.description,
            isDefault: set.is_default,
            active: set.is_active,
            skills: (setSkills.status === 'fulfilled' ? setSkills.value.data ?? [] : []).map((item) => ({
              id: item.skill_id,
              name: item.name,
              description: item.description,
              active: true,
            })),
            mcps: (set.mcps ?? []).map((item) => ({
              serverCode: item.server_code,
              name: item.name || item.server_code,
              description: item.description,
              active: true,
            })),
            clis: (set.clis ?? []).flatMap((item) => {
              const code = item.cli_code ?? item.resource_code ?? item.code;
              return code ? [{ code, name: item.name || code, description: item.description }] : [];
            }),
          } satisfies BotCapabilitySet;
        }),
      );
      sets.push(...details);
    }
    return {
      skills: skills.status === 'fulfilled' ? dataOr(skills.value.data?.items, []).map(mapSkill) : [],
      skillSets: sets,
      mcps: [],
      availableMcps: [],
      marketSkills: [],
      workshopSkills: [],
      resources: resources.status === 'fulfilled' ? resources.value : [],
      screens: screens.status === 'fulfilled' ? dataOr(screens.value.data?.items, []).map(mapScreen) : [],
      routines: routines.status === 'fulfilled' ? dataOr(routines.value.data?.items, []).map(mapRoutine) : [],
      engineConfig: engineConfig.status === 'fulfilled' ? dataOr(engineConfig.value.data, {}) : ({} as BotEngineConfig),
      engineStatus:
        engineStatus.status === 'fulfilled'
          ? {
              engine: engineStatus.value.data?.engine ?? '',
              activeConnections: engineStatus.value.data?.active_connections ?? 0,
              running: engineStatus.value.data?.running ?? false,
            }
          : undefined,
      approvalRequired: approval.status === 'fulfilled' ? Boolean(approval.value.data?.should_approval) : false,
      errors:
        [skills, skillSetResources, resources, screens, routines, engineConfig, engineStatus, approval].filter(
          (item) => item.status === 'rejected',
        ).length + skillSetDetailErrors,
    };
  },
  async loadCapabilityCandidates(botId: string, spaceId?: string) {
    const [boundMcps, mcpServers, repositorySkills, skillCenterSkills, spaceSkills] = await Promise.all([
      botEditorController.listBotMcps(botId),
      botEditorController.listMcpServers(),
      botEditorController.listRepositorySkills(),
      botEditorController.listSkillCenterSkills(),
      spaceId ? listAllConsumableSpaceSkills(spaceId) : Promise.resolve([]),
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
      skillCenterSkills: dataOr(skillCenterSkills.data?.items, []).flatMap((item): BotEditorSkill[] => {
        if (!item.skillCode) return [];
        return [
          {
            id: item.skillCode,
            name: item.skillName || item.skillCode,
            description: item.description,
            version: item.latestVersionNumber === undefined ? undefined : String(item.latestVersionNumber),
            homepageUrl: item.homepageUrl,
            source: 'skillcenter-market',
            active: false,
          },
        ];
      }),
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
  },
  toggleSkill: (botId: string, skill: BotEditorSkill) =>
    botEditorController.setSkillActive(botId, skill.id, !skill.active),
  deleteSkill: (botId: string, skillId: string) => botEditorController.deleteSkill(botId, skillId),
  uploadSkill: (botId: string, file: File) =>
    file.arrayBuffer().then((body) => botEditorController.uploadSkill(botId, body)),
  async uploadSkillFolder(botId: string, files: File[]) {
    const response = await botEditorController.uploadSkillFolder(botId, files);
    const skill = response.data?.skill;
    if (!skill?.skill_id) throw new Error('目录上传成功，但响应中缺少 skill_id');
    return { ...mapSkill(skill), source: 'local' as const };
  },
  setMcpActive: (botId: string, mcp: BotEditorMcp, active: boolean) =>
    botEditorController.setMcpActive(botId, mcp.serverCode, active),
  createSkillSet: (botId: string, name: string) => botEditorController.createSkillSet(botId, { name }),
  updateSkillSet: (botId: string, id: string, name: string) => botEditorController.updateSkillSet(botId, id, { name }),
  deleteSkillSet: (botId: string, id: string) => botEditorController.deleteSkillSet(botId, id),
  setSkillSetActive: (botId: string, set: BotCapabilitySet, active: boolean) =>
    botEditorController.setSkillSetActive(botId, set.id, active),
  setSkillSetSkill: (botId: string, setId: string, skillId: string, active: boolean) =>
    botEditorController.setSkillSetSkill(botId, setId, skillId, active),
  async addSkillCenterReferences(botId: string, setId: string, skillCodes: string[]) {
    const created = await botEditorController.createSkillCenterReferences(
      botId,
      setId,
      [...new Set(skillCodes)].slice(0, 20),
    );
    const requestId = created.data?.request_id;
    if (!requestId) throw new Error('SkillCenter 引用已提交，但响应缺少轮询标识');
    for (let attempt = 0; attempt < 60; attempt += 1) {
      const result = await botEditorController.listSkillCenterReferences(botId, setId, requestId);
      const items = result.data?.items ?? [];
      const failed = items.find((item) => item.status === 'FAILED');
      if (failed) throw new Error(failed.error_message || 'SkillCenter Skill 添加失败');
      if (items.length > 0 && items.every((item) => item.status === 'COMPLETED')) return;
      await new Promise((resolve) => {
        setTimeout(resolve, 2_000);
      });
    }
    throw new Error('SkillCenter Skill 仍在添加中，请稍后刷新查看');
  },
  async setSkillSetMcp(botId: string, setId: string, serverCode: string, active: boolean) {
    if (active) {
      const permission = await botEditorController.getMcpPermission(serverCode);
      if (!permission.data?.has_access) {
        throw new Error('无法添加，请去 MCP 详情页申请权限后重试');
      }
    }
    return botEditorController.setSkillSetMcp(botId, setId, serverCode, active);
  },
  createDirectory: (botId: string, path: string) => botEditorController.createDirectory(botId, path),
  async listResources(botId: string, directory = '') {
    return (
      (await botEditorController.listResources(botId, directory)).data?.items?.map((item) =>
        mapResource(item, directory),
      ) ?? []
    );
  },
  deleteResource: (botId: string, path: string) => botEditorController.deleteResource(botId, path),
  uploadResource: (botId: string, path: string, file: File, overwrite = false) =>
    file.arrayBuffer().then((body) => botEditorController.uploadResource(botId, path, body, overwrite)),
  async previewResource(botId: string, path: string) {
    return (await botEditorController.previewResource(botId, path)).data?.content ?? '';
  },
  downloadResource: (botId: string, path: string) => botEditorController.downloadResource(botId, path),
  downloadResourceDirectory: (botId: string, path: string) =>
    botEditorController.downloadResourceDirectory(botId, path),
  createScreen: (botId: string, input: BotRenderScreenInput) =>
    botEditorController.createRenderScreen(botId, { name: input.name, cdn_url: input.cdnUrl }),
  updateScreen: (botId: string, id: number, input: BotRenderScreenInput) =>
    botEditorController.updateRenderScreen(botId, id, { name: input.name, cdn_url: input.cdnUrl }),
  deleteScreen: (botId: string, id: number) => botEditorController.deleteRenderScreen(botId, id),
  async listLifecycle(botId: string) {
    return (await botEditorController.getLifecycle(botId)).data?.items.map(mapPublication) ?? [];
  },
  upgradeLifecycle: (botId: string, publicationId: number) =>
    botEditorController.upgradeLifecycle(botId, publicationId),
  advanceLifecycle: (botId: string, stage: 'prestable' | 'online') =>
    botEditorController.advanceLifecycle(botId, stage),
  restartLifecycle: (botId: string, stage: 'prestable' | 'online') =>
    botEditorController.restartLifecycle(botId, stage),
  cancelStaging: (botId: string) => botEditorController.cancelStaging(botId),
  offlineLifecycle: (botId: string) => botEditorController.offlineLifecycle(botId),
  retryLifecycle: (botId: string) => botEditorController.retryLifecycle(botId),
  deleteLifecycleDraft: (botId: string) => botEditorController.deleteLifecycleDraft(botId),
  createRoutine: (botId: string, input: BotEditorRoutineInput) =>
    botEditorController.createRoutine(botId, toRoutineWrite(input)),
  updateRoutine: (botId: string, id: string, input: BotEditorRoutineInput) =>
    botEditorController.updateRoutine(botId, id, toRoutineWrite(input)),
  deleteRoutine: (botId: string, id: string) => botEditorController.deleteRoutine(botId, id),
  runRoutine: (botId: string, id: string) => botEditorController.runRoutine(botId, id),
  async listRoutineRuns(botId: string, id: string) {
    const response = await botEditorController.listRoutineRuns(botId, id);
    return (response.data?.items ?? []).map((run) => ({
      id: run.run_id,
      status: run.status,
      startedAt: run.started_at,
      finishedAt: run.finished_at,
    }));
  },
  saveEngineConfig: (botId: string, config: BotEngineConfig) => botEditorController.updateEngineConfig(botId, config),
  saveApproval: (botId: string, enabled: boolean) => botEditorController.updateApprovalConfig(botId, enabled),
};
