/**
 * Engine Adapter 模块
 *
 * 提供统一的引擎特性管理接口
 * 支持不同引擎的差异化功能配置
 *
 * @example
 * // 使用工厂获取适配器
 * const adapter = engineAdapterFactory.getAdapter('openclaw');
 * const features = adapter.getFeatures();
 *
 * @example
 * // 计算特定 Bot 的功能特性
 * const botFeatures = adapter.computeFeatures({
 *   isDefaultBot: false,
 *   isServiceBot: false,
 * });
 *
 * @example
 * // 检查特定功能是否支持
 * const canDelete = adapter.supports('canDelete', { isDefaultBot: false });
 *
 * @example
 * // 便捷的 Hook 用法
 * import { useFeatureSupport } from './adapters/engine';
 *
 * const canDelete = useFeatureSupport('openclaw', 'canDelete', { isDefaultBot: false });
 * const canUpgradeToService = useFeatureSupport(
 *   bot.active_engine,
 *   'canUpgradeToService',
 *   { isDefaultBot: bot.bot_id === 'default' }
 * );
 */

// 类型定义
export { AICodingAdapter } from './AICodingAdapter';
// 基础适配器类
export { BaseEngineAdapter } from './BaseEngineAdapter';
export { ClaudeCodeAdapter } from './ClaudeCodeAdapter';
// 工厂和便捷函数
export {
  engineAdapterFactory,
  useBotFeatures,
  useEngineAdapter,
  useEngineDefaultModelId,
  useFeatureSupport,
} from './EngineAdapterFactory';
export { HermesAdapter } from './HermesAdapter';
// 具体引擎适配器
export { OpenClawAdapter } from './OpenClawAdapter';
export type {
  BotFeatures,
  ChatRenderMode,
  ConditionalFeature,
  EngineAdapter,
  EngineAdapterFactory,
  EngineFeatureConfig,
  FeatureContext,
  FeatureGuard,
} from './types';
