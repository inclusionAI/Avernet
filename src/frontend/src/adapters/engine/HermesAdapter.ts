import { ENGINE_TYPE } from '@/services/backend-api/BotController';
import { BaseEngineAdapter } from './BaseEngineAdapter';

export class HermesAdapter extends BaseEngineAdapter {
  readonly engineType = ENGINE_TYPE.HERMES;
  protected readonly displayName = 'Hermes';
  protected readonly isBeta = true;

  // 模型默认值由后端控制，设置为空字符串让getDefaultModelId返回null
  // 这样会自动使用 enterprise_default 或列表第一个模型
  protected readonly defaultModelId = '';

  protected readonly baseFeatures = {
    canDelete: true,
    canUpgradeToService: false,
    showCapabilityTab: true,
    showResourceTab: true,
    resourceEditable: true,
    showMarkdownConfig: false,
    showNodeManagement: false,
    showChannelManagement: false,
    showEngineConfig: false,
    canRestartBot: true,
    canRestartEngine: true,
    showHealthCheck: false,
    canPublishToMarket: false,
    availableInCronTask: false,
    availableInSkillSet: true,
    showInCreateBotModal: true,
    availableForServiceBot: false,
    showPermissionMode: false,
    showChannelStage: false,
    showSecondaryScreenConfig: false,
    showProjectBoardTab: false,
    showAppConfigTab: false,
    canJoinBcn: false,
    chatRenderMode: 'openclaw' as const,
    enableImageUpload: false,
    showHarnessFlow: false,
    messagePageSize: 0,
  };
}
