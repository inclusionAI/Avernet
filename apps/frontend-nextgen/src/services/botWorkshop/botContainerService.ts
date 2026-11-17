import {
  listBotContainers,
  restartBotContainer,
  scaleBotContainers,
  type BotContainerDto,
  type BotContainerListDto,
  type BotContainerScaleApprovalDto,
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

export interface ContainerScaleApproval {
  status: string;
  approvalUrl?: string;
  currentCount?: number;
  delta?: number;
  targetCount?: number;
}

export interface ContainerSnapshot {
  botId: string;
  summary: { total: number; healthy: number; abnormal: number; restarting: number; unknown: number };
  instances: ContainerInstance[];
  scaleApproval?: ContainerScaleApproval;
}

export interface ContainerScaleResult {
  botId: string;
  accepted: boolean;
  currentCount: number;
  delta: number;
  targetCount: number;
  approval: ContainerScaleApproval;
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

function mapScaleApproval(dto?: BotContainerScaleApprovalDto | null): ContainerScaleApproval | undefined {
  if (!dto) return undefined;
  return {
    status: dto.status,
    approvalUrl: optionalText(dto.approval_url),
    currentCount: dto.current_count ?? undefined,
    delta: dto.delta ?? undefined,
    targetCount: dto.target_count ?? undefined,
  };
}

function mapSnapshot(dto: BotContainerListDto): ContainerSnapshot {
  return {
    botId: dto.bot_id,
    summary: dto.summary,
    instances: dto.instances.map(mapInstance),
    scaleApproval: mapScaleApproval(dto.scale_approval),
  };
}

export const botContainerService = {
  /** ownerId: Bot 拥有者工号,分享/协作场景按拥有者圈定查找范围(自己的 Bot 传本人同值)。 */
  async list(botId: string, userId: string, ownerId?: string): Promise<ContainerSnapshot> {
    const response = await listBotContainers(botId, userId, ownerId);
    if (!response.data) throw new Error('容器实例数据不可用');
    return mapSnapshot(response.data);
  },
  async restart(botId: string, instanceId: string, userId: string) {
    const response = await restartBotContainer(botId, instanceId, userId);
    if (!response.data?.accepted) throw new Error('实例重启未被接受');
  },
  async scale(botId: string, delta: number, userId: string, ownerId?: string): Promise<ContainerScaleResult> {
    const response = await scaleBotContainers(botId, delta, userId, ownerId);
    const data = response.data;
    if (!data?.accepted || !data.approval) throw new Error('扩缩容审批未被接受');
    return {
      botId: data.bot_id,
      accepted: data.accepted,
      currentCount: data.current_count,
      delta: data.delta,
      targetCount: data.target_count,
      approval: mapScaleApproval(data.approval)!,
    };
  },
};
