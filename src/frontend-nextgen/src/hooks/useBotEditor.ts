import type {
  BotCapabilitySet,
  BotEditorEngineStatus,
  BotEditorMcp,
  BotEditorResource,
  BotEditorRoutine,
  BotEditorRoutineInput,
  BotEditorRoutineRun,
  BotEditorSkill,
  BotEngineConfig,
  BotRenderScreen,
  BotRenderScreenInput,
} from '@/domain/botEditor';
import { botEditorService } from '@/services/botWorkshop/botEditorService';
import { useCallback, useEffect, useState } from 'react';
import { toast } from 'sonner';

export function useBotEditor(botId: string | null, serviceBot = false, spaceId?: string, enabled = true) {
  const [skills, setSkills] = useState<BotEditorSkill[]>([]);
  const [skillSets, setSkillSets] = useState<BotCapabilitySet[]>([]);
  const [mcps, setMcps] = useState<BotEditorMcp[]>([]);
  const [availableMcps, setAvailableMcps] = useState<BotEditorMcp[]>([]);
  const [marketSkills, setMarketSkills] = useState<BotEditorSkill[]>([]);
  const [skillCenterSkills, setSkillCenterSkills] = useState<BotEditorSkill[]>([]);
  const [workshopSkills, setWorkshopSkills] = useState<BotEditorSkill[]>([]);
  const [resources, setResources] = useState<BotEditorResource[]>([]);
  const [resourceLoadingPaths, setResourceLoadingPaths] = useState<string[]>([]);
  const [screens, setScreens] = useState<BotRenderScreen[]>([]);
  const [routines, setRoutines] = useState<BotEditorRoutine[]>([]);
  const [engineConfig, setEngineConfig] = useState<BotEngineConfig>({});
  const [engineStatus, setEngineStatus] = useState<BotEditorEngineStatus>();
  const [approvalRequired, setApprovalRequired] = useState(false);
  const [routineRuns, setRoutineRuns] = useState<BotEditorRoutineRun[]>([]);
  const [mcpCallTypes, setMcpCallTypes] = useState<Record<string, 'caller' | 'owner'>>({});
  const [callerContextEditable, setCallerContextEditable] = useState(false);
  const [updatingCallType, setUpdatingCallType] = useState<string>();
  const [loading, setLoading] = useState(Boolean(botId));
  const load = useCallback(async () => {
    if (!botId || !enabled) return;
    setLoading(true);
    try {
      const data = await botEditorService.load(botId, serviceBot);
      setSkills(data.skills);
      setSkillSets(data.skillSets);
      setMcps(data.mcps);
      setResources(data.resources);
      setScreens(data.screens);
      setRoutines(data.routines);
      setEngineConfig(data.engineConfig);
      setEngineStatus(data.engineStatus);
      setApprovalRequired(data.approvalRequired);
      if (serviceBot) {
        const callerContext = await botEditorService.getCallerContext(botId).catch(() => undefined);
        setMcpCallTypes(callerContext?.mcpCallTypes ?? {});
        setCallerContextEditable(callerContext?.editable ?? false);
      } else {
        setMcpCallTypes({});
        setCallerContextEditable(false);
      }
      if (data.errors) toast.warning(`${data.errors} 个配置模块暂时无法加载`);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : '编辑配置加载失败');
    } finally {
      setLoading(false);
    }
  }, [botId, enabled, serviceBot, spaceId]);
  useEffect(() => {
    setAvailableMcps([]);
    setMarketSkills([]);
    setSkillCenterSkills([]);
    setWorkshopSkills([]);
    void load();
  }, [botId, load]);
  const loadCapabilityCandidates = useCallback(async () => {
    if (!botId) return;
    try {
      const candidates = await botEditorService.loadCapabilityCandidates(botId, spaceId);
      setAvailableMcps(candidates.availableMcps);
      setMarketSkills(candidates.marketSkills);
      setSkillCenterSkills(candidates.skillCenterSkills);
      setWorkshopSkills(candidates.workshopSkills);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : '可选能力加载失败');
      throw error;
    }
  }, [botId, spaceId]);
  const act = useCallback(
    async (work: () => Promise<unknown>, message: string) => {
      try {
        await work();
        toast.success(message);
        await load();
      } catch (error) {
        toast.error(error instanceof Error ? error.message : '操作失败');
        throw error;
      }
    },
    [load],
  );
  return {
    skills,
    skillSets,
    mcps,
    availableMcps,
    marketSkills,
    skillCenterSkills,
    workshopSkills,
    loadCapabilityCandidates,
    resources,
    screens,
    routines,
    engineConfig,
    engineStatus,
    approvalRequired,
    setEngineConfig,
    loading,
    reload: load,
    toggleSkill: (skill: BotEditorSkill) =>
      act(() => botEditorService.toggleSkill(botId!, skill), skill.active ? 'Skill 已停用' : 'Skill 已启用'),
    deleteSkill: (id: string) => act(() => botEditorService.deleteSkill(botId!, id), 'Skill 已删除'),
    uploadSkill: (file: File) => act(() => botEditorService.uploadSkill(botId!, file), 'Skill 已上传'),
    setMcpActive: (mcp: BotEditorMcp, active: boolean) =>
      act(() => botEditorService.setMcpActive(botId!, mcp, active), active ? 'MCP 已添加' : 'MCP 已移除'),
    createDirectory: (path: string) => act(() => botEditorService.createDirectory(botId!, path), '目录已创建'),
    browseResources: async (directory = '') => {
      try {
        setResources(await botEditorService.listResources(botId!, directory));
      } catch (error) {
        toast.error(error instanceof Error ? error.message : '资源目录加载失败');
      }
    },
    deleteResource: (path: string) => act(() => botEditorService.deleteResource(botId!, path), '资源已删除'),
    uploadResource: (path: string, file: File, overwrite = false) =>
      act(() => botEditorService.uploadResource(botId!, path, file, overwrite), '资源已上传'),
    previewResource: (path: string) => botEditorService.previewResource(botId!, path),
    downloadResource: async (path: string, type: BotEditorResource['type']) => {
      try {
        const blob =
          type === 'folder'
            ? await botEditorService.downloadResourceDirectory(botId!, path)
            : await botEditorService.downloadResource(botId!, path);
        const url = URL.createObjectURL(blob);
        const anchor = document.createElement('a');
        anchor.href = url;
        const name = path.split('/').pop() || 'resource';
        anchor.download = type === 'folder' ? `${name}.zip` : name;
        anchor.click();
        URL.revokeObjectURL(url);
      } catch (error) {
        toast.error(error instanceof Error ? error.message : type === 'folder' ? '文件夹下载失败' : '文件下载失败');
      }
    },
    saveScreen: (input: BotRenderScreenInput, id?: number) =>
      act(
        () => (id ? botEditorService.updateScreen(botId!, id, input) : botEditorService.createScreen(botId!, input)),
        id ? '副屏配置已更新' : '副屏配置已创建',
      ),
    createSkillSet: (name: string) => act(() => botEditorService.createSkillSet(botId!, name), '能力集已创建'),
    updateSkillSet: (id: string, name: string) =>
      act(() => botEditorService.updateSkillSet(botId!, id, name), '能力集已更新'),
    deleteSkillSet: (id: string) => act(() => botEditorService.deleteSkillSet(botId!, id), '能力集已删除'),
    setSkillSetActive: (set: BotCapabilitySet, active: boolean) =>
      act(() => botEditorService.setSkillSetActive(botId!, set, active), active ? '能力集已启用' : '能力集已停用'),
    setSkillSetSkill: (setId: string, skillId: string, active: boolean) =>
      act(
        () => botEditorService.setSkillSetSkill(botId!, setId, skillId, active),
        active ? 'Skill 已加入能力集' : 'Skill 已移出能力集',
      ),
    addSkillCenterReferences: (setId: string, skillCodes: string[]) =>
      act(() => botEditorService.addSkillCenterReferences(botId!, setId, skillCodes), 'SkillCenter Skill 已加入能力集'),
    uploadSkillFolder: async (files: File[]) => {
      try {
        const skill = await botEditorService.uploadSkillFolder(botId!, files);
        setSkills((current) => [skill, ...current.filter((item) => item.id !== skill.id)]);
        toast.success('本地 Skill 已上传，请勾选后确认添加');
        return skill;
      } catch (error) {
        toast.error(error instanceof Error ? error.message : '本地 Skill 目录上传失败');
        throw error;
      }
    },
    setSkillSetMcp: (setId: string, serverCode: string, active: boolean) =>
      act(
        () => botEditorService.setSkillSetMcp(botId!, setId, serverCode, active),
        active ? 'MCP 已加入能力集' : 'MCP 已移出能力集',
      ),
    mcpCallTypes,
    callerContextEditable,
    updatingCallType,
    updateMcpCallType: async (serverCode: string, callType: 'caller' | 'owner') => {
      setUpdatingCallType(serverCode);
      try {
        const applied = await botEditorService.updateMcpCallType(botId!, serverCode, callType);
        setMcpCallTypes((current) => ({ ...current, [serverCode]: applied }));
        toast.success('调用身份已更新');
      } catch (error) {
        toast.error(error instanceof Error ? error.message : '调用身份更新失败');
        throw error;
      } finally {
        setUpdatingCallType(undefined);
      }
    },
    loadResourceDirectory: async (directory: string) => {
      setResourceLoadingPaths((current) => [...new Set([...current, directory])]);
      try {
        const children = await botEditorService.listResources(botId!, directory);
        setResources((current) => {
          const merged = new Map(current.map((item) => [item.path, item]));
          children.forEach((item) => merged.set(item.path, item));
          return [...merged.values()];
        });
      } catch (error) {
        toast.error(error instanceof Error ? error.message : '资源目录加载失败');
        throw error;
      } finally {
        setResourceLoadingPaths((current) => current.filter((path) => path !== directory));
      }
    },
    resourceLoadingPaths,
    deleteScreen: (id: number) => act(() => botEditorService.deleteScreen(botId!, id), '副屏配置已删除'),
    saveRoutine: (input: BotEditorRoutineInput, id?: string) =>
      act(
        () => (id ? botEditorService.updateRoutine(botId!, id, input) : botEditorService.createRoutine(botId!, input)),
        id ? '定时任务已更新' : '定时任务已创建',
      ),
    toggleRoutine: (routine: BotEditorRoutine) =>
      act(
        () => botEditorService.updateRoutine(botId!, routine.id, { ...routine, enabled: !routine.enabled }),
        routine.enabled ? '定时任务已停用' : '定时任务已启用',
      ),
    deleteRoutine: (id: string) => act(() => botEditorService.deleteRoutine(botId!, id), '定时任务已删除'),
    runRoutine: (id: string) => act(() => botEditorService.runRoutine(botId!, id), '已触发执行'),
    routineRuns,
    loadRoutineRuns: async (id: string) => {
      try {
        setRoutineRuns(await botEditorService.listRoutineRuns(botId!, id));
      } catch (error) {
        toast.error(error instanceof Error ? error.message : '执行记录加载失败');
      }
    },
    saveEngineConfig: () => act(() => botEditorService.saveEngineConfig(botId!, engineConfig), '引擎配置已保存'),
    saveApproval: (enabled: boolean) => act(() => botEditorService.saveApproval(botId!, enabled), '发布审批配置已更新'),
  };
}
