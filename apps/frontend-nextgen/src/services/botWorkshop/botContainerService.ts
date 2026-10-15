import {
  listBotContainers,
  restartBotContainer,
  type BotContainerDto,
  type BotContainerListDto,
  type BotContainerStatus,
} from '@/services/backendApi/bots/botContainerController';

export type ContainerInstanceStatus = BotContainerStatus;

export interface ContainerInstance {
  id: string;
  node?: string;
  cpu?: string;
  memory?: string;
  status: ContainerInstanceStatus;
  internalStatus?: string;
  engine: string;
  provider?: string;
  providerInstanceId?: string;
  createdAt?: string;
}

export interface ContainerSnapshot {
  botId: string;
  summary: { total: number; healthy: number; abnormal: number; restarting: number; unknown: number };
  instances: ContainerInstance[];
}

function optionalText(value: string | null | undefined) {
  return value?.trim() || undefined;
}

function mapInstance(item: BotContainerDto): ContainerInstance {
  return {
    id: item.id,
    node: optionalText(item.node),
    cpu: optionalText(item.cpu),
    memory: optionalText(item.memory),
    status: item.status,
    internalStatus: optionalText(item.internal_status),
    engine: item.engine,
    provider: optionalText(item.provider),
    providerInstanceId: optionalText(item.provider_instance_id),
    createdAt: optionalText(item.created_at),
  };
}

function mapSnapshot(dto: BotContainerListDto): ContainerSnapshot {
  return { botId: dto.bot_id, summary: dto.summary, instances: dto.instances.map(mapInstance) };
}

export const botContainerService = {
  async list(botId: string): Promise<ContainerSnapshot> {
    const response = await listBotContainers(botId);
    if (!response.data) throw new Error('容器实例数据不可用');
    return mapSnapshot(response.data);
  },
  async restart(botId: string, instanceId: string) {
    const response = await restartBotContainer(botId, instanceId);
    if (!response.data?.accepted) throw new Error('实例重启未被接受');
  },
};
