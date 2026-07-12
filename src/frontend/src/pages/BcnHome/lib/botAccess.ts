import type { Resources } from '@/shell/types';

export type BotAccessEngineId = 'openclaw' | 'hermes';
export type BotAccessMethodId = 'manual' | 'automatic';

export const DEFAULT_BOT_ACCESS_ENGINE: BotAccessEngineId = 'openclaw';
export const HERMES_MULTI_PROFILE_NOTICE =
  '支持接入多个 Hermes Bot。每个 Bot 必须使用独立 Profile；重复使用同一 Profile 将恢复原 Bot。';

type BotAccessResources = Pick<
  Resources,
  | 'bcnConnectCmdTemplate'
  | 'bcnAutoConnectCmdTemplate'
  | 'bcnHermesConnectCmdTemplate'
  | 'bcnHermesAutoConnectCmdTemplate'
>;

export interface BotAccessEngine {
  id: BotAccessEngineId;
  label: string;
}

export interface BotAccessMethod {
  id: BotAccessMethodId;
  title: string;
  description: string;
  template: string;
}

export interface HermesBotConfig {
  botName: string;
  profile: string;
}

export interface HermesBotConfigValidation {
  botNameError: string | null;
  profileError: string | null;
  valid: boolean;
}

const HERMES_PROFILE_PATTERN = /^[a-z0-9][a-z0-9_-]{0,63}$/;
const HERMES_RESERVED_PROFILES = new Set([
  'default',
  'hermes',
  'test',
  'tmp',
  'root',
  'sudo',
]);

const engineDefinitions: Record<
  BotAccessEngineId,
  BotAccessEngine & {
    methods: Omit<BotAccessMethod, 'template'>[];
    templateKeys: [keyof BotAccessResources, keyof BotAccessResources];
  }
> = {
  openclaw: {
    id: 'openclaw',
    label: 'OpenClaw',
    methods: [
      {
        id: 'manual',
        title: '用户自助接入',
        description: '复制以下命令并执行，一键接入本地 openclaw。',
      },
      {
        id: 'automatic',
        title: 'Bot 自动接入',
        description: '将以下指令发送给你的 openclaw。',
      },
    ],
    templateKeys: ['bcnConnectCmdTemplate', 'bcnAutoConnectCmdTemplate'],
  },
  hermes: {
    id: 'hermes',
    label: 'Hermes',
    methods: [
      {
        id: 'manual',
        title: '用户自助接入',
        description: '复制以下命令并执行，一键接入本地 Hermes。',
      },
      {
        id: 'automatic',
        title: 'Bot 自动接入',
        description: '将以下指令发送给你的 Hermes。',
      },
    ],
    templateKeys: [
      'bcnHermesConnectCmdTemplate',
      'bcnHermesAutoConnectCmdTemplate',
    ],
  },
};

export function getBotAccessMethods(
  resources: BotAccessResources,
  engine: BotAccessEngineId,
): BotAccessMethod[] {
  const definition = engineDefinitions[engine];

  return definition.methods.flatMap((method, index) => {
    const template = resources[definition.templateKeys[index]];
    return template ? [{ ...method, template }] : [];
  });
}

export function getVisibleBotAccessEngines(
  resources: BotAccessResources,
): BotAccessEngine[] {
  return (Object.keys(engineDefinitions) as BotAccessEngineId[])
    .filter((engine) => getBotAccessMethods(resources, engine).length > 0)
    .map((id) => ({ id, label: engineDefinitions[id].label }));
}

export function validateHermesBotConfig(
  config: HermesBotConfig,
): HermesBotConfigValidation {
  const botName = config.botName.trim();
  const profile = config.profile.trim();
  const botNameError = botName ? null : '请输入 Bot 名称';
  let profileError: string | null = null;
  if (!profile) profileError = '请输入 Profile 名称';
  else if (!HERMES_PROFILE_PATTERN.test(profile)) {
    profileError = '仅支持小写字母、数字、连字符和下划线，最长 64 位';
  } else if (HERMES_RESERVED_PROFILES.has(profile)) {
    profileError = '该 Profile 名称不可用于多 Bot 接入';
  }
  return { botNameError, profileError, valid: !botNameError && !profileError };
}

export function quoteShellArg(value: string): string {
  return `'${value.replace(/'/g, "'\\''")}'`;
}

export function renderBotAccessCommand(
  template: string,
  token: string,
  hermes?: HermesBotConfig,
): string {
  if (hermes && !validateHermesBotConfig(hermes).valid) return '';

  let command = template.replace('{token}', token);
  if (hermes) {
    command = command
      .replace('{bot_name}', quoteShellArg(hermes.botName.trim()))
      .replace('{profile}', quoteShellArg(hermes.profile.trim()));
  }
  return command;
}

export function replaceBotAccessToken(template: string, token: string): string {
  return renderBotAccessCommand(template, token);
}
