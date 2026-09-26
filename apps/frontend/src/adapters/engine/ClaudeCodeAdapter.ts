/**
 * ClaudeCode 引擎适配器
 */
import { ENGINE_TYPE } from '@/services/backend-api/BotController';
import { BaseEngineAdapter } from './BaseEngineAdapter';

/**
 * ClaudeCode 引擎适配器
 */
export class ClaudeCodeAdapter extends BaseEngineAdapter {
  readonly engineType = ENGINE_TYPE.CLAUDECODE;
  protected readonly displayName = 'Claude Code';

  /** 默认模型 ID - Claude Code 引擎默认使用 GLM-5.1 */
  protected readonly defaultModelId = 'GLM-5.1';

  protected readonly baseFeatures = {
    canDelete: true,
    canUpgradeToService: false,
    showCapabilityTab: true, // ClaudeCode 有能力集
    showResourceTab: true,
    resourceEditable: true,
    showMarkdownConfig: true,
    showNodeManagement: false,
    showChannelManagement: false,
    showChannelStage: false,
    showEngineConfig: false, // ClaudeCode 隐藏引擎配置
    canRestartBot: true,
    canRestartEngine: true,
    showHealthCheck: false, // ClaudeCode 隐藏健康检查
    canPublishToMarket: true,
    availableInCronTask: false, // ClaudeCode 不能用于定时任务
    availableInSkillSet: true, // ClaudeCode 可以添加到能力集
    showInCreateBotModal: true,
    availableForServiceBot: true, // ClaudeCode 支持创建服务 Bot
    showPermissionMode: true, // ClaudeCode 展示权限模式选择器
    showSecondaryScreenConfig: false,
    showProjectBoardTab: false,
    showAppConfigTab: false, // 普通 ClaudeCode 不显示应用配置 Tab
    canJoinBcn: false, // ClaudeCode 不允许加入 BCN
    chatRenderMode: 'aicoding' as const, // ClaudeCode 使用和 AICoding 相同的渲染模式
    enableImageUpload: false, // ClaudeCode 不支持图片上传
    showHarnessFlow: false,
    messagePageSize: 0,
  };
}
