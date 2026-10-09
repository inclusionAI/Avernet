import type { BotRuntime } from '@/adapters/bot-runtime';
import type { BotCapabilityProfile } from './types';

export function resolveDefaultBotCapabilityProfile(runtime: BotRuntime): BotCapabilityProfile {
  const unsupportedReasons: Record<string, string> = {};

  if (runtime.engine === 'unknown') {
    unsupportedReasons.publish = '当前 Bot 引擎未识别，暂不支持发布';
    unsupportedReasons.schedule = '当前 Bot 引擎未识别，暂不支持定时任务';
  }

  return {
    canChat: true,
    canPublish: runtime.engine !== 'unknown',
    canSchedule: runtime.engine !== 'unknown',
    canConfigureResources: !runtime.isDefaultBot,
    unsupportedReasons,
  };
}
