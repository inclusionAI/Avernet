import {
  fetchArchitectDomainOptions,
  type ArchitectDomainOption,
} from '@/services/backendApi/architectDomainController';
import { BackendRequestError } from '@/services/backendApi/httpClient';
import {
  getWorkflows,
  searchAntCodeProjects,
  type AntCodeProject,
  type WorkflowItem,
} from '@/services/backendApi/legacyAICodingController';
import { DOMAIN_BOTS_PAGE_SIZE, searchDomainBots, type Bot } from '@/services/backendApi/legacyBotController';
import {
  listCodefuseModelsForUser,
  setCallerCodefuseAuth,
  setCodefuseToken,
  type CodefuseModelDto,
} from '@/services/backendApi/legacyCodefuseController';
import {
  precheckYuqueInit,
  verifyYuqueBinding,
  type YuquePrecheckSource,
  type YuquePrecheckWarning,
} from '@/services/backendApi/legacyYuqueController';

/**
 * AgentCoding 创建表单使用的业务服务边界。
 * 组件只依赖这里的领域数据与用例，不直接感知 legacy API Controller。
 */
export {
  BackendRequestError,
  DOMAIN_BOTS_PAGE_SIZE,
  fetchArchitectDomainOptions,
  getWorkflows,
  listCodefuseModelsForUser,
  precheckYuqueInit,
  searchAntCodeProjects,
  searchDomainBots,
  setCallerCodefuseAuth,
  setCodefuseToken,
  verifyYuqueBinding,
};

export type {
  AntCodeProject,
  ArchitectDomainOption,
  Bot,
  CodefuseModelDto,
  WorkflowItem,
  YuquePrecheckSource,
  YuquePrecheckWarning,
};
