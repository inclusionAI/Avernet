// 完整服务门面保留旧契约和任务接口；公开发现只依赖 publicDiscoveryRuntime。
import type { PublicTaskSearchQuery } from '@/domain/collaborationSquare/types';
import { CollaborationSquareApiAdapter } from './collaborationSquareApiAdapter';
import type { CollaborationSquareGateway } from './collaborationSquareGateway';
import { MockCollaborationSquareAdapter } from './mockCollaborationSquareAdapter';
import { PublicDiscoveryService } from './publicDiscoveryService';
export { CollaborationSquareError } from './collaborationSquareError';
export type { CollaborationSquareServiceOverview } from './publicDiscoveryService';
export { collaborationSquareBotService, collaborationSquareGroupService } from './publicDiscoveryRuntime';

export class CollaborationSquareService extends PublicDiscoveryService {
  constructor(private readonly fullGateway: CollaborationSquareGateway) { super(fullGateway); }
  listPublicTasks(query?: PublicTaskSearchQuery, signal?: AbortSignal) {
    return this.fullGateway.listPublicTasks(query, signal);
  }
  getPublicTask(taskId: string, signal?: AbortSignal) {
    return this.fullGateway.getPublicTask(taskId, signal);
  }
}

export const collaborationSquareService = new CollaborationSquareService(new MockCollaborationSquareAdapter());
export const collaborationSquareTaskService = new CollaborationSquareService(new CollaborationSquareApiAdapter());
