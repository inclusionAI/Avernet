/** CapabilitySetViewer —— 聊天输入框「通用协作能力」能力集只读查看的共享类型。
 *
 * 数据由 useBotCapabilitySets 装配（pages 层），组件层只消费状态不感知好友/团队分类；
 * 明细 skills 按需拉取（fetchSetSkills），单集失败不阻塞其他集展示。 */

/** 能力集内 MCP 成员（来自 skill-sets/resources 内嵌 mcps，只读）。 */
export interface CapabilitySetMcpView {
  serverCode: string;
  name: string;
  description?: string;
}

/** 能力集内 Skill 成员（明细分页拉取，只读）。 */
export interface CapabilitySetSkillView {
  skillId: string;
  name: string;
  description?: string;
}

/** 已激活能力集行（「默认能力集」命名已在数据层归一）。 */
export interface CapabilitySetView {
  id: string;
  name: string;
  description?: string;
  isDefault: boolean;
  mcps: CapabilitySetMcpView[];
}

/** 单集 skills 明细拉取结果（Promise.allSettled 容错：失败不出错、以 failed 标记）。 */
export interface CapabilitySetDetailResult {
  skills: CapabilitySetSkillView[];
  failed: boolean;
}

/** 「通用协作能力」区消费的状态装配（useBotCapabilitySets 返回；好友 Bot / 未装配为 null）。 */
export interface ComposerCapabilitySetsState {
  sets: CapabilitySetView[];
  isLoading: boolean;
  /** popover 每次打开调用重拉（in-flight 去重；不用长驻缓存，见 design D2）。 */
  load: () => void;
  /** 按需拉取单集 skills（明细子面板展开时调用；结果含 failed 标记供重试）。 */
  fetchSetSkills: (setId: string) => Promise<CapabilitySetDetailResult>;
}
