// Bot 公开风险前置检测 Service（bot-public-risk-check）。
// 分层出口（TC-G002）：Hook/业务组件不得直接 import backendApi Controller，
// 统一由此文件转出口；调用时序、client_request_id 幂等与轮询生命周期留在
// useBotPublicRisk，协议与 envelope 细节见 collaborationBotPublicRiskController。
export {
  createBotPublicRiskCheck,
  getCurrentBotPublicRiskCheck,
  refreshBotPublicRiskCheck,
} from '@/services/backendApi/collaboration/collaborationBotPublicRiskController';
export type {
  BotPublicRiskCheckRecord,
  BotPublicRiskRefreshResult,
  BotPublicRiskTarget,
  CreateBotPublicRiskRequest,
} from '@/services/backendApi/collaboration/collaborationBotPublicRiskController';
