import { resolveBotRuntime } from '@/adapters/bot-runtime/resolveBotRuntime';
import {
  isAicodingConnectionEngine,
  type BotConnectionRuntime,
} from '@/services/workspace/botChatProvider';
import type { ChatBotView } from '@/services/workspace/botSessionService';

export type ConversationPresentationVariant = 'standard' | 'friend-runtime-pending' | 'friend-coding-compat';

export interface ConversationPresentation {
  variant: ConversationPresentationVariant;
  composerPlaceholder: string;
  composerExtensions: boolean;
  taskComposer: boolean;
  modelSelector: boolean;
  fileFeatures: boolean;
  resourceManager: boolean;
  sidePane: boolean;
}

const STANDARD_PRESENTATION: ConversationPresentation = {
  variant: 'standard',
  composerPlaceholder: '可使用 /file 引用文件、/link 引用链接、/skill 调用能力',
  composerExtensions: true,
  taskComposer: true,
  modelSelector: true,
  fileFeatures: true,
  resourceManager: true,
  sidePane: true,
};

const FRIEND_RUNTIME_PENDING_PRESENTATION: ConversationPresentation = {
  variant: 'friend-runtime-pending',
  composerPlaceholder: '连接 Bot 后即可开始对话',
  composerExtensions: false,
  taskComposer: false,
  modelSelector: false,
  fileFeatures: false,
  resourceManager: false,
  sidePane: false,
};

const FRIEND_CODING_COMPAT_PRESENTATION: ConversationPresentation = {
  variant: 'friend-coding-compat',
  composerPlaceholder: '请输入你的问题或任务',
  composerExtensions: false,
  taskComposer: false,
  modelSelector: false,
  fileFeatures: false,
  resourceManager: false,
  sidePane: false,
};

function metadataSuggestsFriendCodingBot(bot: ChatBotView): boolean {
  const runtime = resolveBotRuntime({
    botId: bot.botId,
    engine: bot.engine,
    templateType: bot.templateType,
    templateName: bot.templateName,
    botType: bot.botType,
  });
  return runtime.isAgentCodingBot;
}

/**
 * 好友 Bot 使用两阶段展示判定：目录 metadata 仅用于提前识别 Coding Bot，
 * /connection.engine 返回后覆盖目录推断。目录无法确认时先关闭普通 CC 扩展能力，
 * 避免连接完成前请求 /skills、资源管理或副屏能力。
 */
export function resolveConversationPresentation(
  bot: ChatBotView | null,
  connectionRuntime: BotConnectionRuntime = { resolved: false, engine: null },
): ConversationPresentation {
  if (!bot?.isFriendBot) return STANDARD_PRESENTATION;

  if (connectionRuntime.resolved) {
    return isAicodingConnectionEngine(connectionRuntime.engine) ? FRIEND_CODING_COMPAT_PRESENTATION : STANDARD_PRESENTATION;
  }

  return metadataSuggestsFriendCodingBot(bot) ? FRIEND_CODING_COMPAT_PRESENTATION : FRIEND_RUNTIME_PENDING_PRESENTATION;
}
