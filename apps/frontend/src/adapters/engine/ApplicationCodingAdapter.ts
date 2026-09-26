/**
 * ApplicationCoding 应用 Coding Bot 引擎适配器
 *
 * 应用 Coding Bot 是 Claude Code 的一种特殊模式（template_type = 'applicationCoding'）
 * 与普通 Claude Code Bot 有以下差异：
 * - 使用三栏布局（会话/能力集 + 对话 + 文件树）
 * - 隐藏资源 Tab
 * - 隐藏"我发起的"和"他人发起的"Tab
 * - 其他管理功能可能也有所不同
 *
 * 注意：EngineAdapterFactory 会根据 templateType 自动选择此 Adapter，
 * 因此此 Adapter 无需在 computeFeatures 中再次判断 templateType。
 */
import type { EngineType } from '@/services/backend-api/BotController';
import { BaseEngineAdapter } from './BaseEngineAdapter';

/**
 * 应用 Coding Bot 引擎适配器
 */
export class ApplicationCodingAdapter extends BaseEngineAdapter {
  // 使用独特的 engineType 以避免覆盖 ClaudeCodeAdapter
  readonly engineType = 'applicationCoding' as EngineType;
  protected readonly displayName = '应用 Coding';

  /** 默认模型 ID */
  protected readonly defaultModelId = 'GLM-5.1';

  protected readonly baseFeatures = {
    canDelete: true,
    canUpgradeToService: false,
    showCapabilityTab: true,
    showResourceTab: false, // 应用 Coding 隐藏资源 Tab
    resourceEditable: true,
    showMarkdownConfig: false,
    showNodeManagement: false,
    showChannelManagement: false,
    showChannelStage: false,
    showEngineConfig: false,
    canRestartBot: true,
    canRestartEngine: true,
    showHealthCheck: false,
    canPublishToMarket: false,
    availableInCronTask: false,
    availableInSkillSet: false, // 应用 Coding 不能添加到能力集
    showInCreateBotModal: false, // 不在创建 Bot 弹窗中显示（通过 template_type 创建）
    availableForServiceBot: false,
    showPermissionMode: true,
    showSecondaryScreenConfig: false,
    showProjectBoardTab: false,
    showAppConfigTab: true, // 应用 Coding 显示应用配置 Tab
    canJoinBcn: false, // 应用 Coding 不允许加入 BCN
    chatRenderMode: 'aicoding' as const,
    enableImageUpload: false, // 应用 Coding 不支持图片上传
    showHarnessFlow: false,
    messagePageSize: 500, // 消息接口每次请求的 limit 参数（500条）
  };
}
