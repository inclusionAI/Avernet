import {
  getCollaborationTemplateYaml as getYamlApi,
  listCollaborationTemplates as listApi,
  type CollaborationTemplateSummary,
} from '@/services/backendApi/collaboration/collaborationTemplateController';
import type { BackendApiEnvelope } from '@/services/backendApi/types';
import type { DomainError, DomainResult } from './identityService';

export type CollaborationTemplate = CollaborationTemplateSummary;

export interface CollaborationTemplateListResult {
  templates: CollaborationTemplateSummary[];
  tagLabels: Record<string, Record<string, string>>;
  defaultLanguage: string;
}

function toDomainError(code: string, friendlyMessage: string): DomainError {
  return { code, friendlyMessage, canRetry: false };
}

/** YAML 端点兼容 text/plain 裸字符串和历史 JSON envelope。 */
function unwrapYaml(resp: unknown): string {
  if (typeof resp === 'string') return resp;
  const envelope = resp as BackendApiEnvelope<unknown> | undefined;
  const data = envelope?.data;
  return typeof data === 'string' ? data : String(data ?? '');
}

/**
 * 自定义协作模板数据层。Controller 返回统一 envelope，本 service 解包出 data 并映射为领域结构。
 * 组件不直接依赖 DTO/envelope，只消费 DomainResult。
 */
export const collaborationTemplateService = {
  async list(): Promise<DomainResult<CollaborationTemplateListResult>> {
    try {
      const resp = await listApi();
      const data = resp.data;
      return {
        ok: true,
        data: {
          templates: data?.templates ?? [],
          tagLabels: data?.tag_labels ?? {},
          defaultLanguage: data?.default_language ?? 'zh-CN',
        },
      };
    } catch {
      return {
        ok: false,
        error: toDomainError('TEMPLATES_LOAD_FAILED', '加载协作模板列表失败，请稍后重试。'),
      };
    }
  },

  async getYaml(templateId: string, lang?: string): Promise<DomainResult<string>> {
    try {
      // 预发返回 text/plain，历史 mock/网关可能仍返回 envelope，统一解包。
      const resp = await getYamlApi({ template_id: templateId, lang });
      const yaml = unwrapYaml(resp);
      return { ok: true, data: yaml };
    } catch {
      return {
        ok: false,
        error: toDomainError('TEMPLATE_YAML_LOAD_FAILED', '加载模板内容失败，请稍后重试。'),
      };
    }
  },
};
