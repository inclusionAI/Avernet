import { backendRequest } from '../httpClient';
import type { BackendApiEnvelope } from '../types';

/** 自定义协作模板参与者定义。 */
export interface CollaborationTemplateParticipant {
  display_name: string;
  description: string;
  required: boolean;
}

/** 自定义协作模板列表项。 */
export interface CollaborationTemplateSummary {
  id: string;
  name: string;
  description: string;
  participants: Record<string, CollaborationTemplateParticipant>;
  tags: string[];
  priority: number;
  available_languages: string[];
}

/** 自定义协作模板列表响应（统一包裹在 envelope.data 中）。 */
export interface CollaborationTemplatesData {
  templates: CollaborationTemplateSummary[];
  tag_labels: Record<string, Record<string, string>>;
  default_language: string;
  supported_languages: string[];
}

const BASE = '/api/v1/collaboration/templates';

/** 获取自定义协作模板列表：GET /api/v1/collaboration/templates。 */
export function listCollaborationTemplates(options?: { [key: string]: unknown }) {
  return backendRequest<BackendApiEnvelope<CollaborationTemplatesData>>(BASE, {
    method: 'GET',
    ...(options || {}),
    injectUserId: false,
  });
}

/** 获取自定义协作模板 YAML 内容（纯文本）：GET /api/v1/collaboration/templates/{template_id}?lang={lang}。
 * 网关将模板 YAML 以 text/plain 返回（非 JSON envelope），故用 responseType:'text' 按文本读取，
 * 返回值即 YAML 字符串本身；切勿再当 envelope 取 .data（会导致空串）。 */
export function getCollaborationTemplateYaml(
  params: { template_id: string; lang?: string },
  options?: { [key: string]: unknown },
) {
  const query = params.lang ? { lang: params.lang } : undefined;
  return backendRequest<string>(`${BASE}/${encodeURIComponent(params.template_id)}`, {
    method: 'GET',
    responseType: 'text',
    params: query,
    injectUserId: false,
    ...(options || {}),
  });
}
