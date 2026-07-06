/**
 * Engine Adapter 类型定义
 *
 * 提供统一的引擎特性接口，支持不同引擎的差异化功能配置
 */
import type { EngineType } from '@/services/backend-api/BotController';

/**
 * 消息渲染模式
 * - 'openclaw': OpenClaw 协议渲染（流式消息合并）
 * - 'aicoding': AICoding 协议渲染（工具调用、thinking、连续消息分离渲染）
 */
export type ChatRenderMode = 'openclaw' | 'aicoding' | 'teclaw';

/**
 * Bot 功能特性配置
 */
export interface BotFeatures {
  /** 是否可删除 */
  canDelete: boolean;
  /** 是否可转化为服务 Bot */
  canUpgradeToService: boolean;
  /** 是否展示能力集 Tab */
  showCapabilityTab: boolean;
  /** 是否展示资源 Tab */
  showResourceTab: boolean;
  /** 资源 Tab 是否可编辑（桌面 Bot 只读） */
  resourceEditable: boolean;
  /** 是否展示 MD 文档配置 */
  showMarkdownConfig: boolean;
  /** 是否展示节点管理 */
  showNodeManagement: boolean;
  /** 是否展示渠道管理 */
  showChannelManagement: boolean;
  /** 是否展示渠道生效环境选择（仅服务 Bot） */
  showChannelStage: boolean;
  /** 是否可查看/编辑引擎配置 */
  showEngineConfig: boolean;
  /** 是否可重启 Bot */
  canRestartBot: boolean;
  /** 是否可重启引擎 */
  canRestartEngine: boolean;
  /** 是否展示健康检查 */
  showHealthCheck: boolean;
  /** 是否可发布到广场 */
  canPublishToMarket: boolean;
  /** 是否可在定时任务中选择 */
  availableInCronTask: boolean;
  /** 是否可在添加到能力集弹窗中选择 */
  availableInSkillSet: boolean;
  /** 创建 Bot 弹窗中是否展示此引擎类型 */
  showInCreateBotModal: boolean;
  /** 创建服务 Bot 时是否可用此引擎 */
  availableForServiceBot: boolean;
  /** 是否展示权限模式选择器 */
  showPermissionMode: boolean;
  /** 是否展示副屏配置 Tab */
  showSecondaryScreenConfig: boolean;
  /** 是否在 AssistantSidebar 展示项目看板 Tab（仅 AICoding 引擎） */
  showProjectBoardTab: boolean;
  /** 是否展示应用配置 Tab（仅应用 Bot） */
  showAppConfigTab: boolean;
  /** 是否允许加入 BCN 协作网络（决定 GroupChat BotTab 是否展示） */
  canJoinBcn: boolean;
  /** 消息渲染模式 */
  chatRenderMode: ChatRenderMode;
  /** 是否支持图片上传 */
  enableImageUpload: boolean;
  /** 是否展示 HarnessFlow 工作流按钮 */
  showHarnessFlow: boolean;
  /**
   * 消息接口请求的 limit 参数（每次拉取多少条消息）。
   * - 0：使用消费方默认值（ChatPanel 默认 500）
   * - 正整数：指定 limit 值
   *
   * 注意：此字段仅控制请求参数 limit，不控制是否启用分页。
   * 分页逻辑由 API 返回的 total 字段动态判断（total > 已加载数 → hasMore）。
   *
   * 不能用 supports() 判断，需直接读取 adapter.getFeatures().messagePageSize。
   */
  messagePageSize: number;
}

/**
 * 引擎特性配置（包含所有特性）
 */
export interface EngineFeatureConfig extends BotFeatures {
  /** 引擎类型标识 */
  engineType: EngineType;
  /** 引擎显示名称 */
  displayName: string;
  /** 引擎图标 */
  icon?: string;
  /** 引擎描述 */
  description?: string;
  /** 是否为 Beta 功能 */
  isBeta?: boolean;
  /** 消息渲染模式 */
  chatRenderMode: ChatRenderMode;
}

/**
 * 特性计算上下文
 * 某些特性需要根据 Bot 的具体状态动态计算
 */
export interface FeatureContext {
  /** 是否为系统默认 Bot */
  isDefaultBot?: boolean;
  /** 是否是服务 Bot 类型 */
  isServiceBot?: boolean;
  /** 是否是桌面 Bot（bot_type === 'desktop'） */
  isDesktopBot?: boolean;
  /** Bot 类型 */
  botType?: string;
  /** 其他自定义上下文 */
  [key: string]: any;
}

/**
 * 引擎适配器接口
 * 每个引擎需要实现此接口
 */
export interface EngineAdapter {
  /** 引擎类型 */
  readonly engineType: EngineType;

  /** 获取引擎完整特性配置 */
  getFeatures(): EngineFeatureConfig;

  /**
   * 计算特定 Bot 的功能特性
   * @param context Bot 上下文信息
   */
  computeFeatures(context: FeatureContext): BotFeatures;

  /**
   * 检查是否支持特定功能
   * @param feature 功能名称（仅布尔类型的特性，chatRenderMode 需直接读取）
   * @param context Bot 上下文
   */
  supports(
    feature: keyof Omit<BotFeatures, 'chatRenderMode' | 'messagePageSize'>,
    context?: FeatureContext,
  ): boolean;

  /**
   * 获取消息渲染模式
   * 用于区分不同引擎的消息渲染协议
   * @returns ChatRenderMode 'aicoding' | 'openclaw'
   */
  getChatRenderMode(): ChatRenderMode;

  /**
   * 获取该引擎的默认模型 ID
   * 返回 null 表示使用全局默认（enterprise_default 或第一个模型）
   * @returns 模型 ID 字符串或 null
   */
  getDefaultModelId(): string | null;
}

/**
 * 引擎适配器工厂接口
 */
export interface EngineAdapterFactory {
  /**
   * 获取指定引擎的适配器
   * @param engineType 引擎类型
   */
  getAdapter(engineType: EngineType): EngineAdapter;

  /**
   * 注册新的引擎适配器
   * @param adapter 引擎适配器实例
   */
  register(adapter: EngineAdapter): void;

  /**
   * 检查是否支持指定引擎
   * @param engineType 引擎类型
   */
  hasAdapter(engineType: EngineType): boolean;

  /**
   * 获取所有已注册的引擎类型
   */
  getAllEngineTypes(): EngineType[];

  /**
   * 获取所有支持的引擎特性配置
   */
  getAllFeatures(): EngineFeatureConfig[];
}

/**
 * 特性守卫函数类型
 */
export type FeatureGuard = (context: FeatureContext) => boolean;

/**
 * 带条件的特性配置
 */
export interface ConditionalFeature {
  /** 基础值 */
  defaultValue: boolean;
  /** 条件覆盖规则（后面的覆盖前面的） */
  overrides?: Array<{
    /** 条件判断 */
    when: FeatureGuard;
    /** 条件满足时的值 */
    value: boolean;
  }>;
}
