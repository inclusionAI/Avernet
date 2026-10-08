import { botCapabilityDetailController as api } from '@/services/backendApi/bots/botCapabilityDetailController';
import { isEnvelopeFailure, type BackendApiEnvelope } from '@/services/backendApi/types';

export interface CapabilityDetailTarget {
  kind: 'skill' | 'mcp';
  id: string;
  name: string;
}
export interface CapabilityDetail {
  name: string;
  description: string;
  content: string;
  tools: Array<{ name: string; description: string; parameters?: string }>;
}

function unwrap<T>(response: BackendApiEnvelope<T>): T {
  if (isEnvelopeFailure(response) || !response.data) throw new Error(response.message || '能力详情加载失败');
  return response.data;
}
const text = (value: unknown) => (typeof value === 'string' ? value : '');
const record = (value: unknown): Record<string, unknown> =>
  value !== null && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : {};

export const botCapabilityDetailService = {
  async load(botId: string, target: CapabilityDetailTarget, ownerId?: string): Promise<CapabilityDetail> {
    if (target.kind === 'skill') {
      const [metadata, body] = await Promise.all([
        api.skill(botId, target.id, ownerId),
        api.skillContent(botId, target.id, ownerId),
      ]);
      const skill = unwrap(metadata);
      return { name: skill.name, description: skill.description || '', content: unwrap(body).content, tools: [] };
    }
    const server = unwrap(await api.mcp(target.id));
    return {
      name: text(server.name) || target.name,
      description: text(server.description),
      content: text(record(server.docs).overview) || text(server.docs),
      tools: (Array.isArray(server.tools) ? server.tools : []).map((value) => {
        const tool = record(value);
        const parameters = tool.inputSchema ?? tool.input_schema ?? tool.parameters;
        return {
          name: text(tool.name),
          description: text(tool.description),
          parameters: parameters ? JSON.stringify(parameters, null, 2) : undefined,
        };
      }),
    };
  },
};
