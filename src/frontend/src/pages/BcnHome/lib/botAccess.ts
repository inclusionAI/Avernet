import type { Resources } from '@/shell/types';

export type BotAccessEngineId = 'openclaw' | 'hermes';
export type BotAccessMethodId = 'manual' | 'automatic';

export const DEFAULT_BOT_ACCESS_ENGINE: BotAccessEngineId = 'openclaw';

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

export function getVisibleBotAccessEngines(
  resources: BotAccessResources,
): BotAccessEngine[] {
  return (Object.keys(engineDefinitions) as BotAccessEngineId[])
    .filter((engine) => getBotAccessMethods(resources, engine).length > 0)
    .map((id) => ({ id, label: engineDefinitions[id].label }));
}

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

export function replaceBotAccessToken(template: string, token: string): string {
  return template.replace('{token}', token);
}
