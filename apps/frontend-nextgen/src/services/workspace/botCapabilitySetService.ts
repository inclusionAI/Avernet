/** botCapabilitySetService —— 单聊「通用协作能力」只读查询(Hook → Service → API Controller)。
 * 只读复用 BotWorkshop 同源接口(design D2):listSkillSetResources 一次取全量集 + 内嵌 mcps,
 * 单集 skills 明细按需逐集拉取。DTO → CapabilitySetView 的映射归一在本层完成
 * (「默认能力集」命名归一、默认集置顶展示);失败语义(failed 标记 / toast 提示)留在 Hook 装配层。 */
import type { CapabilitySetSkillView, CapabilitySetView } from '@/components/Workspace/CapabilitySetViewer/types';
import { botEditorController } from '@/services/backendApi/bots/botEditorController';

/** 拉取 Bot 已激活能力集(过滤 is_active;默认集置顶,展示顺序与 botEditorService 同语义)。
 *  ownerId 透传 controller 可选语义(团队 Bot 实体必须由调用方显式传 bot.ownerId)。 */
export async function listActiveCapabilitySets(botId: string, ownerId?: string): Promise<CapabilitySetView[]> {
  const res = await botEditorController.listSkillSetResources(botId, ownerId);
  const activeSets: CapabilitySetView[] = (res.data ?? [])
    .filter((set) => set.is_active)
    .map((set) => ({
      id: set.id,
      name: set.is_default ? '默认能力集' : set.name,
      description: set.description,
      isDefault: set.is_default,
      mcps: (set.mcps ?? []).map((mcp) => ({
        serverCode: mcp.server_code,
        name: mcp.name || mcp.server_code,
        description: mcp.description,
      })),
    }));
  activeSets.sort((a, b) => Number(b.isDefault) - Number(a.isDefault));
  return activeSets;
}

/** 拉取单集 skills 明细(透传 controller 异常,由调用方封装 failed 容错不阻塞其他集)。 */
export async function listCapabilitySetSkills(
  botId: string,
  setId: string,
  ownerId?: string,
): Promise<CapabilitySetSkillView[]> {
  const res = await botEditorController.listSkillSetSkills(botId, setId, ownerId);
  return (res.data ?? []).map((skill) => ({
    skillId: skill.skill_id,
    name: skill.name || skill.skill_id,
    description: skill.description,
  }));
}