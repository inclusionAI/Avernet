/**
 * AICoding 引擎适配器
 */
import { ENGINE_TYPE } from '@/services/backend-api/BotController';
import { BaseEngineAdapter } from './BaseEngineAdapter';

/**
 * AICoding 引擎适配器
 * 限制较多的引擎类型
 */
export class AICodingAdapter extends BaseEngineAdapter {
  readonly engineType = ENGINE_TYPE.AICODING;
  protected readonly displayName = 'AICoding';
  protected readonly isBeta = true;

  /** 默认模型 ID - AICoding 引擎默认使用 GLM-5 */
  // NOTE: 模型默认值由后端控制
  protected readonly defaultModelId = '';

  protected readonly baseFeatures = {
    canDelete: true,
    canUpgradeToService: false,
    showCapabilityTab: true, // AICoding 支持能力集
    showResourceTab: true,
    resourceEditable: true,
    showMarkdownConfig: false,
    showNodeManagement: false,
    showChannelManagement: false,
    showChannelStage: false,
    showEngineConfig: false, // AICoding 隐藏引擎配置
    canRestartBot: true,
    canRestartEngine: false, // AICoding 隐藏重启引擎
    showHealthCheck: false, // AICoding 隐藏健康检查（与 ClaudeCode/Hermes 保持一致）
    canPublishToMarket: false,
    availableInCronTask: false, // AICoding 不能用于定时任务
    availableInSkillSet: true, // AICoding 支持添加到能力集
    showInCreateBotModal: true,
    availableForServiceBot: false,
    showPermissionMode: true, // AICoding 展示权限模式选择器
    showSecondaryScreenConfig: false,
    showProjectBoardTab: true, // AICoding 在侧栏展示项目看板
    showAppConfigTab: false, // AICoding 不显示应用配置 Tab
    canJoinBcn: false, // AICoding 不允许加入 BCN
    chatRenderMode: 'aicoding' as const, // AICoding 使用 AICoding 渲染模式
    enableImageUpload: false, // AICoding 不支持图片上传
    showHarnessFlow: false,
    messagePageSize: 500, // 消息接口每次请求的 limit 参数（500条）
  };
}
