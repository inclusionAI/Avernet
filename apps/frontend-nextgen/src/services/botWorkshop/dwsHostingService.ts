import type { HostingConfig, HostingSettings } from '@/domain/dwsHosting';
import { dwsHostingController } from '@/services/backendApi/bots/dwsHostingController';
import type { BackendApiEnvelope } from '@/services/backendApi/types';

function data<T>(value: BackendApiEnvelope<T>): T {
  if (value.data === null || value.data === undefined || Number(value.code) >= 400000) {
    throw new Error(value.message || '消息托管返回异常');
  }
  return value.data;
}
function supportedSettings(settings: HostingSettings): HostingSettings {
  // Only serialize the published fields; ignore stale labels from older tabs.
  return {
    rules: settings.rules,
    window: settings.window,
    custom_window: settings.custom_window,
    timezone: 'Asia/Shanghai',
    persona: settings.persona,
  };
}
function normalizeConfig(value: HostingConfig): HostingConfig {
  return {
    ...value,
    settings: supportedSettings(value.settings),
  };
}
export const dwsHostingService = {
  eligibility: async (...args: Parameters<typeof dwsHostingController.eligibility>) => {
    const result = await dwsHostingController.eligibility(...args);
    return result.code === 200000 && result.data?.available === true;
  },
  enable: async (...args: Parameters<typeof dwsHostingController.enable>) =>
    data(await dwsHostingController.enable(...args)),
  authorize: async (...args: Parameters<typeof dwsHostingController.authorize>) =>
    data(await dwsHostingController.authorize(...args)),
  check: async (...args: Parameters<typeof dwsHostingController.check>) =>
    data(await dwsHostingController.check(...args)),
  takeover: async (...args: Parameters<typeof dwsHostingController.takeover>) =>
    data(await dwsHostingController.takeover(...args)),
  status: async (...args: Parameters<typeof dwsHostingController.status>) =>
    data(await dwsHostingController.status(...args)),
  settings: async (...args: Parameters<typeof dwsHostingController.settings>) =>
    normalizeConfig(data(await dwsHostingController.settings(...args))),
  save: async (botId: string, version: number, settings: HostingSettings) =>
    normalizeConfig(data(await dwsHostingController.save(botId, version, supportedSettings(settings)))),
  conversations: async (...args: Parameters<typeof dwsHostingController.conversations>) =>
    data(await dwsHostingController.conversations(...args)),
};
