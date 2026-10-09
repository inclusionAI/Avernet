import { ENGINE_TYPE } from '@/services/backend-api/BotController';
import { BaseEngineAdapter } from './BaseEngineAdapter';
import type { FeatureContext } from './types';

export class TeclawAdapter extends BaseEngineAdapter {
  readonly engineType = ENGINE_TYPE.TECLAW;
  protected readonly displayName = 'TEClaw';

  protected readonly defaultModelId = '';

  protected readonly baseFeatures = {
    canDelete: true,
    canUpgradeToService: {
      defaultValue: true,
      overrides: [
        {
          when: (ctx: FeatureContext) => ctx.isDefaultBot === true,
          value: false,
        },
        {
          when: (ctx: FeatureContext) => ctx.isServiceBot === true,
          value: false,
        },
        {
          when: (ctx: FeatureContext) => ctx.isDesktopBot === true,
          value: false,
        },
      ],
    },
    showCapabilityTab: true,
    showResourceTab: true,
    resourceEditable: {
      defaultValue: true,
      overrides: [
        {
          when: (ctx: FeatureContext) => ctx.isDesktopBot === true,
          value: false,
        },
      ],
    },
    showMarkdownConfig: true,
    showNodeManagement: false,
    showChannelManagement: true,
    showChannelStage: {
      defaultValue: false,
      overrides: [
        {
          when: (ctx: FeatureContext) => ctx.isServiceBot === true,
          value: true,
        },
      ],
    },
    showEngineConfig: true,
    canRestartBot: false,
    canRestartEngine: false,
    showHealthCheck: true,
    canPublishToMarket: true,
    availableInCronTask: true,
    availableInSkillSet: true,
    showInCreateBotModal: true,
    availableForServiceBot: {
      defaultValue: true,
      overrides: [
        {
          when: (ctx: FeatureContext) => ctx.isDefaultBot === true,
          value: false,
        },
        {
          when: (ctx: FeatureContext) => ctx.isDesktopBot === true,
          value: false,
        },
      ],
    },
    showPermissionMode: false,
    showSecondaryScreenConfig: false,
    showProjectBoardTab: false,
    showAppConfigTab: false,
    canJoinBcn: false,
    chatRenderMode: 'teclaw' as const,
    enableImageUpload: {
      defaultValue: true,
      overrides: [
        {
          when: (ctx: FeatureContext) => ctx.isDesktopBot === true,
          value: false,
        },
      ],
    },
  };
}
