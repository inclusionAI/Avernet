import {
  createLocalBot,
  getLocalDirectory,
  getLocalStartProgress,
  listLocalDevices,
  openLocalFolder,
  pollLocalAuthorization,
  type LocalBotCreateRequest,
} from '@/services/backendApi/bots/localBotController';
import { mapBotDto } from './botMapper';
import type { BotCreateAuthorizationPollResult, BotCreateInput, BotCreateResult } from './types';

export interface DesktopDevice {
  id: string;
  name: string;
  status: string;
}
export function localCreateRequest(input: BotCreateInput): LocalBotCreateRequest {
  if (!['openclaw', 'hermes'].includes(input.engine)) throw new Error('本地 Bot 仅支持 OpenClaw 和 Hermes');
  if (!input.local?.machineId || !input.local.mountPath) throw new Error('请选择设备并等待工作目录就绪');
  return {
    bot_name: input.name.trim(),
    bot_desc: input.description.trim(),
    engine: input.engine,
    machine_id: input.local.machineId,
    mount_path: input.local.mountPath,
  };
}
export const localBotService = {
  async devices(): Promise<DesktopDevice[]> {
    const items: DesktopDevice[] = [];
    for (let page = 1; ; page += 1) {
      const response = await listLocalDevices(page);
      if (!response.data) throw new Error('设备接口未返回数据');
      items.push(
        ...response.data.items.map((d) => ({
          id: d.machine_id,
          name: d.machine_name || d.hostname || d.machine_id,
          status: d.status,
        })),
      );
      if (items.length >= response.data.total) return items;
      if (!response.data.items.length) throw new Error('设备列表分页不完整，请刷新');
    }
  },
  async directory(machineId: string) {
    const response = await getLocalDirectory(machineId);
    if (!response.data?.absolute_path) throw new Error('设备未返回有效工作目录');
    return response.data.absolute_path;
  },
  async create(input: BotCreateInput): Promise<BotCreateResult> {
    const request = localCreateRequest(input);
    const response = await createLocalBot(request);
    const dto = response.data;
    if (!dto) throw new Error('创建接口未返回 Bot 数据');
    if ('iframe_url' in dto || 'redirect_url' in dto) {
      const botId = typeof dto.bot_id === 'string' ? dto.bot_id : '';
      const iframeUrl = typeof dto.iframe_url === 'string' ? dto.iframe_url : '';
      const redirectUrl = typeof dto.redirect_url === 'string' ? dto.redirect_url : '';
      if (!botId || (!iframeUrl && !redirectUrl)) throw new Error('授权信息不完整');
      return { type: 'authorization_required', botId, iframeUrl, redirectUrl, request };
    }
    return { type: 'created', bot: mapBotDto({ ...dto, bot_type: 'desktop' }).item };
  },
  async poll(botId: string, request: LocalBotCreateRequest): Promise<BotCreateAuthorizationPollResult> {
    const response = await pollLocalAuthorization(botId, request);
    const dto = response.data;
    if (!dto?.status) throw new Error('授权接口未返回状态');
    return {
      status: dto.status,
      message: dto.message,
      bot: dto.bot ? mapBotDto({ ...dto.bot, bot_type: 'desktop' }, botId).item : undefined,
    };
  },
  async progress(botId: string) {
    const response = await getLocalStartProgress(botId);
    if (!response.data) throw new Error('启动进度不可用');
    return response.data;
  },
  openFolder: openLocalFolder,
};
