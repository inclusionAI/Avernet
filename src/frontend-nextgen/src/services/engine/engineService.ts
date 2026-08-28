import { resolveBotRuntime } from '@/adapters/bot-runtime';

export interface EngineServiceOverview {
  module: string;
  description: string;
}

// 引擎 Service 收口 BotRuntime 解析，不让页面直接判断组合字段。
export const engineService = {
  getOverview(): EngineServiceOverview {
    void resolveBotRuntime;
    return { module: 'engine', description: '引擎 Service 收口 BotRuntime 解析，不让页面直接判断组合字段。' };
  },
};
