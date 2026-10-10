import { BotConfigApiGateway } from './botConfigApiGateway';
import { BotConfigService } from './botConfigService';

/** Lab 社区配置服务单例（仿 communityService 组合范式）。 */
export const botConfigService = new BotConfigService(new BotConfigApiGateway());
export type { BotConfigGateway } from './botConfigGateway';
export { BotConfigService, BROWSE_NOTE_MAX_LENGTH } from './botConfigService';
