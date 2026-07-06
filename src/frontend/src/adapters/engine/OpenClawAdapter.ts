/**
 * OpenClaw 引擎适配器
 */
import { ENGINE_TYPE } from '@/services/backend-api/BotController';
import { BaseEngineAdapter } from './BaseEngineAdapter';
import type { FeatureContext } from './types';

/**
 * OpenClaw 引擎适配器
 * 支持默认 Bot、云端 Bot、桌面 Bot 的不同特性
 * 云端 Bot 和桌面 Bot 通过 FeatureContext.isDesktopBot 区分
 */
export class OpenClawAdapter extends BaseEngineAdapter {
  readonly engineType = ENGINE_TYPE.OPENCLAW;
  protected readonly displayName = 'OpenClaw';

  /** 默认模型 ID - OpenClaw 引擎默认使用 Kimi-K2.5 */
  // NOTE: 模型默认值由后端控制
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
    showMarkdownConfig: {
      defaultValue: true,
      overrides: [
        {
          when: (ctx: FeatureContext) => ctx.isDesktopBot === true,
          value: false,
        },
      ],
    },
    showNodeManagement: {
      defaultValue: true,
      overrides: [
        {
          when: (ctx: FeatureContext) => ctx.isDesktopBot === true,
          value: false,
        },
      ],
    },
    showChannelManagement: {
      defaultValue: true,
      overrides: [
        {
          when: (ctx: FeatureContext) => ctx.isDesktopBot === true,
          value: false,
        },
      ],
    },
    showChannelStage: {
      defaultValue: false,
      overrides: [
        {
          when: (ctx: FeatureContext) => ctx.isServiceBot === true,
          value: true,
        },
      ],
    },
    showEngineConfig: {
      defaultValue: true,
      overrides: [
        {
          when: (ctx: FeatureContext) => ctx.isDesktopBot === true,
          value: false,
        },
      ],
    },
    canRestartBot: true,
    canRestartEngine: true,
    showHealthCheck: {
      defaultValue: true,
      overrides: [
        {
          when: (ctx: FeatureContext) => ctx.isDesktopBot === true,
          value: false,
        },
      ],
    },
    canPublishToMarket: {
      defaultValue: true,
      overrides: [
        {
          when: (ctx: FeatureContext) => ctx.isDesktopBot === true,
          value: false,
        },
      ],
    },
    availableInCronTask: true,
    availableInSkillSet: {
      defaultValue: true,
      overrides: [
        {
          when: (ctx: FeatureContext) => ctx.isDesktopBot === true,
          value: false,
        },
      ],
    },
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
    showSecondaryScreenConfig: true,
    showProjectBoardTab: false,
    showAppConfigTab: false, // OpenClaw 不显示应用配置 Tab
    canJoinBcn: true, // 云端 OC + 桌面 OC 均允许加入 BCN，BotTab 仅展示这类 Bot
    chatRenderMode: 'openclaw' as const, // OpenClaw 使用 OpenClaw 渲染模式
    enableImageUpload: {
      defaultValue: true, // 默认支持图片上传
      overrides: [
        {
          when: (ctx: FeatureContext) => ctx.isDesktopBot === true,
          value: false, // 桌面 Bot 不支持图片上传
        },
      ],
    },
    showHarnessFlow: {
      defaultValue: true,
      overrides: [
        {
          when: (ctx: FeatureContext) => ctx.isDesktopBot === true,
          value: false,
        },
      ],
    },
    messagePageSize: 0,
  };
}
