/**
 * 基础引擎适配器
 *
 * 提供默认的引擎特性实现，新引擎可继承此类并覆盖特定方法
 */
import type { EngineType } from '@/services/backend-api/BotController';
import type {
  BotFeatures,
  ChatRenderMode,
  ConditionalFeature,
  EngineAdapter,
  EngineFeatureConfig,
  FeatureContext,
} from './types';

/**
 * 解析条件特性
 * 支持布尔值或条件特性配置，字符串类型的特性直接返回布尔值
 */
function resolveConditionalFeature(
  feature: boolean | ConditionalFeature | string | undefined,
  context: FeatureContext,
): boolean {
  // undefined 返回 false
  if (feature === undefined) {
    return false;
  }

  if (typeof feature === 'boolean') {
    return feature;
  }

  // 如果是字符串（如 ChatRenderMode），返回 true 以通过类型检查
  // 字符串类型特性应单独处理，不通过此函数
  if (typeof feature === 'string') {
    return true;
  }

  // 按顺序应用覆盖规则
  if (feature.overrides) {
    for (const override of feature.overrides) {
      if (override.when(context)) {
        return override.value;
      }
    }
  }

  return feature.defaultValue;
}

/**
 * 基础引擎适配器类
 * 新引擎适配器应继承此类
 */
export abstract class BaseEngineAdapter implements EngineAdapter {
  /** 引擎类型，子类必须定义 */
  abstract readonly engineType: EngineType;

  /** 引擎显示名称 */
  protected abstract readonly displayName: string;

  /** 引擎描述 */
  protected readonly description?: string;

  /** 是否为 Beta 功能 */
  protected readonly isBeta?: boolean = false;

  /**
   * 默认模型 ID
   * 如果设置，该引擎的 Bot 将默认使用此模型
   * 为 null 时使用全局默认（enterprise_default 或第一个模型）
   */
  protected readonly defaultModelId?: string | null = null;

  /**
   * 基础特性配置
   * 子类可覆盖这些默认值，支持条件特性配置
   *
   * 类型说明：
   * - 布尔特性：`boolean | ConditionalFeature`
   * - `chatRenderMode`：`ChatRenderMode`（字符串，直取原值）
   * - `messagePageSize`：`number`（数字，直取原值）
   */
  protected readonly baseFeatures: Record<
    keyof BotFeatures,
    boolean | ConditionalFeature | ChatRenderMode | number
  > = {
    canDelete: true,
    canUpgradeToService: false,
    showCapabilityTab: true,
    showResourceTab: true,
    resourceEditable: true,
    showMarkdownConfig: true,
    showNodeManagement: true,
    showChannelManagement: true,
    showChannelStage: false,
    showEngineConfig: true,
    canRestartBot: true,
    canRestartEngine: true,
    showHealthCheck: true,
    canPublishToMarket: true,
    availableInCronTask: true,
    availableInSkillSet: true,
    showInCreateBotModal: true,
    availableForServiceBot: false,
    showPermissionMode: false,
    showSecondaryScreenConfig: false,
    showProjectBoardTab: false,
    showAppConfigTab: false,
    canJoinBcn: true,
    chatRenderMode: 'openclaw' as ChatRenderMode,
    enableImageUpload: false,
    showHarnessFlow: false,
    messagePageSize: 0, // 默认 0，消费方使用默认值 500
  };

  /**
   * 获取引擎完整特性配置
   * 解析所有条件特性为最终布尔值
   */
  getFeatures(): EngineFeatureConfig {
    // 默认上下文（所有条件判断为 false）
    const defaultContext: FeatureContext = {};

    return {
      engineType: this.engineType,
      displayName: this.displayName,
      description: this.description,
      isBeta: this.isBeta,
      canDelete: resolveConditionalFeature(
        this.baseFeatures.canDelete,
        defaultContext,
      ),
      canUpgradeToService: resolveConditionalFeature(
        this.baseFeatures.canUpgradeToService,
        defaultContext,
      ),
      showCapabilityTab: resolveConditionalFeature(
        this.baseFeatures.showCapabilityTab,
        defaultContext,
      ),
      showResourceTab: resolveConditionalFeature(
        this.baseFeatures.showResourceTab,
        defaultContext,
      ),
      resourceEditable: resolveConditionalFeature(
        this.baseFeatures.resourceEditable,
        defaultContext,
      ),
      showMarkdownConfig: resolveConditionalFeature(
        this.baseFeatures.showMarkdownConfig,
        defaultContext,
      ),
      showNodeManagement: resolveConditionalFeature(
        this.baseFeatures.showNodeManagement,
        defaultContext,
      ),
      showChannelManagement: resolveConditionalFeature(
        this.baseFeatures.showChannelManagement,
        defaultContext,
      ),
      showChannelStage: resolveConditionalFeature(
        this.baseFeatures.showChannelStage,
        defaultContext,
      ),
      showEngineConfig: resolveConditionalFeature(
        this.baseFeatures.showEngineConfig,
        defaultContext,
      ),
      canRestartBot: resolveConditionalFeature(
        this.baseFeatures.canRestartBot,
        defaultContext,
      ),
      canRestartEngine: resolveConditionalFeature(
        this.baseFeatures.canRestartEngine,
        defaultContext,
      ),
      showHealthCheck: resolveConditionalFeature(
        this.baseFeatures.showHealthCheck,
        defaultContext,
      ),
      canPublishToMarket: resolveConditionalFeature(
        this.baseFeatures.canPublishToMarket,
        defaultContext,
      ),
      availableInCronTask: resolveConditionalFeature(
        this.baseFeatures.availableInCronTask,
        defaultContext,
      ),
      availableInSkillSet: resolveConditionalFeature(
        this.baseFeatures.availableInSkillSet,
        defaultContext,
      ),
      showInCreateBotModal: resolveConditionalFeature(
        this.baseFeatures.showInCreateBotModal,
        defaultContext,
      ),
      availableForServiceBot: resolveConditionalFeature(
        this.baseFeatures.availableForServiceBot,
        defaultContext,
      ),
      showPermissionMode: resolveConditionalFeature(
        this.baseFeatures.showPermissionMode,
        defaultContext,
      ),
      showSecondaryScreenConfig: resolveConditionalFeature(
        this.baseFeatures.showSecondaryScreenConfig,
        defaultContext,
      ),
      showProjectBoardTab: resolveConditionalFeature(
        this.baseFeatures.showProjectBoardTab,
        defaultContext,
      ),
      canJoinBcn: resolveConditionalFeature(
        this.baseFeatures.canJoinBcn,
        defaultContext,
      ),
      // chatRenderMode 是字符串类型，直接取原值，不使用 resolveConditionalFeature
      chatRenderMode: this.baseFeatures.chatRenderMode as ChatRenderMode,
      enableImageUpload: resolveConditionalFeature(
        this.baseFeatures.enableImageUpload,
        defaultContext,
      ),
      showHarnessFlow: resolveConditionalFeature(
        this.baseFeatures.showHarnessFlow,
        defaultContext,
      ),
      // messagePageSize 是数字类型，直接取原值
      messagePageSize: this.baseFeatures.messagePageSize as number,
    };
  }

  /**
   * 计算特定 Bot 的功能特性
   * 子类可覆盖此方法实现动态特性计算
   */
  computeFeatures(context: FeatureContext): BotFeatures {
    const base = this.baseFeatures;

    return {
      canDelete: context.isDefaultBot
        ? false
        : this.computeFeature('canDelete', base.canDelete, context),
      canUpgradeToService: this.computeFeature(
        'canUpgradeToService',
        base.canUpgradeToService,
        context,
      ),
      showCapabilityTab: this.computeFeature(
        'showCapabilityTab',
        base.showCapabilityTab,
        context,
      ),
      showResourceTab: this.computeFeature(
        'showResourceTab',
        base.showResourceTab,
        context,
      ),
      resourceEditable: this.computeFeature(
        'resourceEditable',
        base.resourceEditable,
        context,
      ),
      showMarkdownConfig: this.computeFeature(
        'showMarkdownConfig',
        base.showMarkdownConfig,
        context,
      ),
      showNodeManagement: this.computeFeature(
        'showNodeManagement',
        base.showNodeManagement,
        context,
      ),
      showChannelManagement: this.computeFeature(
        'showChannelManagement',
        base.showChannelManagement,
        context,
      ),
      showChannelStage: this.computeFeature(
        'showChannelStage',
        base.showChannelStage,
        context,
      ),
      showEngineConfig: this.computeFeature(
        'showEngineConfig',
        base.showEngineConfig,
        context,
      ),
      canRestartBot: this.computeFeature(
        'canRestartBot',
        base.canRestartBot,
        context,
      ),
      canRestartEngine: this.computeFeature(
        'canRestartEngine',
        base.canRestartEngine,
        context,
      ),
      showHealthCheck: this.computeFeature(
        'showHealthCheck',
        base.showHealthCheck,
        context,
      ),
      canPublishToMarket: this.computeFeature(
        'canPublishToMarket',
        base.canPublishToMarket,
        context,
      ),
      availableInCronTask: this.computeFeature(
        'availableInCronTask',
        base.availableInCronTask,
        context,
      ),
      availableInSkillSet: this.computeFeature(
        'availableInSkillSet',
        base.availableInSkillSet,
        context,
      ),
      showInCreateBotModal: this.computeFeature(
        'showInCreateBotModal',
        base.showInCreateBotModal,
        context,
      ),
      availableForServiceBot: this.computeFeature(
        'availableForServiceBot',
        base.availableForServiceBot,
        context,
      ),
      showPermissionMode: this.computeFeature(
        'showPermissionMode',
        base.showPermissionMode,
        context,
      ),
      showSecondaryScreenConfig: this.computeFeature(
        'showSecondaryScreenConfig',
        base.showSecondaryScreenConfig,
        context,
      ),
      showProjectBoardTab: this.computeFeature(
        'showProjectBoardTab',
        base.showProjectBoardTab,
        context,
      ),
      showAppConfigTab: this.computeFeature(
        'showAppConfigTab',
        base.showAppConfigTab,
        context,
      ),
      canJoinBcn: this.computeFeature('canJoinBcn', base.canJoinBcn, context),
      // chatRenderMode 是字符串类型，直接取原值
      chatRenderMode: base.chatRenderMode as ChatRenderMode,
      enableImageUpload: this.computeFeature(
        'enableImageUpload',
        base.enableImageUpload,
        context,
      ),
      showHarnessFlow: this.computeFeature(
        'showHarnessFlow',
        base.showHarnessFlow,
        context,
      ),
      // messagePageSize 是数字类型，直接取原值
      messagePageSize: base.messagePageSize as number,
    };
  }

  /**
   * 计算单个特性
   * 子类可覆盖此方法实现特定特性的动态计算
   */
  protected computeFeature(
    _featureName: keyof BotFeatures,
    defaultValue: boolean | ConditionalFeature | ChatRenderMode | number,
    context: FeatureContext,
  ): boolean {
    // 默认实现：解析条件特性
    // 注意：chatRenderMode（字符串）和 messagePageSize（数字）不是布尔值，不会调用此方法
    return resolveConditionalFeature(
      defaultValue as boolean | ConditionalFeature,
      context,
    );
  }

  /**
   * 检查是否支持特定功能
   * 注意：`chatRenderMode` 返回的是字符串，请使用 `getChatRenderMode()` / `getFeatures()`
   */
  supports(
    feature: keyof Omit<BotFeatures, 'chatRenderMode' | 'messagePageSize'>,
    context?: FeatureContext,
  ): boolean {
    if (context) {
      const features = this.computeFeatures(context);
      return features[feature] as boolean;
    }
    return this.getFeatures()[feature] as boolean;
  }

  /**
   * 获取消息渲染模式
   * 直接返回 chatRenderMode 值
   */
  getChatRenderMode(): ChatRenderMode {
    return this.baseFeatures.chatRenderMode as ChatRenderMode;
  }

  /**
   * 获取该引擎的默认模型 ID
   * 默认返回 null，表示使用全局默认（enterprise_default 或第一个模型）
   * 子类可通过设置 defaultModelId 属性或覆盖此方法指定引擎特定的默认模型
   */
  getDefaultModelId(): string | null {
    return this.defaultModelId ?? null;
  }
}
