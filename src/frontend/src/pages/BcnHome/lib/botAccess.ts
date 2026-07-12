import type { Resources } from '@/shell/types';

export type BotAccessEngineId = 'openclaw' | 'hermes';
export type BotAccessMethodId = 'manual' | 'automatic';

export const DEFAULT_BOT_ACCESS_ENGINE: BotAccessEngineId = 'openclaw';
export const HERMES_MULTI_PROFILE_NOTICE =
  '支持接入多个 Hermes Bot。Avernet 会根据 Bot 名称自动创建独立 Profile；相同名称将恢复原 Bot。';

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
}

export interface HermesBotConfigValidation {
  botNameError: string | null;
  valid: boolean;
}

const HERMES_PROFILE_PREFIX = 'avernet-';
const HERMES_PROFILE_MAX_LENGTH = 64;

function fnv1a32(value: string): string {
  let hash = 0x811c9dc5;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 0x01000193);
  }
  return (hash >>> 0).toString(16).padStart(8, '0');
}

export function deriveHermesProfile(botName: string): string {
  const trimmed = botName.trim();
  if (!trimmed) return '';

  const slug = trimmed
    .normalize('NFKD')
    .toLowerCase()
    .replace(/[\u0300-\u036f]/g, '')
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');

  if (!slug) return `${HERMES_PROFILE_PREFIX}bot-${fnv1a32(trimmed)}`;

  const maxSlugLength = HERMES_PROFILE_MAX_LENGTH - HERMES_PROFILE_PREFIX.length;
  const shortened = slug.slice(0, maxSlugLength).replace(/-+$/g, '');
  return `${HERMES_PROFILE_PREFIX}${shortened}`;
}

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
  const botNameError = config.botName.trim() ? null : '请输入 Bot 名称';
  return { botNameError, valid: !botNameError };
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
      .replace('{profile}', quoteShellArg(deriveHermesProfile(hermes.botName)));
  }
  return command;
}

export function replaceBotAccessToken(template: string, token: string): string {
  return renderBotAccessCommand(template, token);
}
