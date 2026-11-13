// 公开 Bot/群共享运行时：legacy 门面再导出同一实例，避免好友请求去重状态分裂。
import { PublicDiscoveryApiAdapter } from './publicDiscoveryApiAdapter';
import { PublicDiscoveryService } from './publicDiscoveryService';
export { CollaborationSquareError } from './collaborationSquareError';

export const collaborationSquareBotService = new PublicDiscoveryService(new PublicDiscoveryApiAdapter());
export const collaborationSquareGroupService = new PublicDiscoveryService(new PublicDiscoveryApiAdapter());
