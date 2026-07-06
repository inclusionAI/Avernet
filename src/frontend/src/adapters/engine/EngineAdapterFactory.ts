/**
 * Engine Adapter 工厂
 *
 * 管理和提供所有引擎适配器实例
 * 支持运行时注册新的引擎适配器
 */
import type { EngineType } from '@/services/backend-api/BotController';
import { AICodingAdapter } from './AICodingAdapter';
import { ApplicationCodingAdapter } from './ApplicationCodingAdapter';
import { ClaudeCodeAdapter } from './ClaudeCodeAdapter';
import { HermesAdapter } from './HermesAdapter';
import { OpenClawAdapter } from './OpenClawAdapter';
import { TeclawAdapter } from './TeclawAdapter';
import type {
  BotFeatures,
  EngineAdapter,
  EngineAdapterFactory,
  FeatureContext,
} from './types';

/**
 * 引擎适配器工厂实现
 * 单例模式确保全局唯一实例
 */
class EngineAdapterFactoryImpl implements EngineAdapterFactory {
  private adapters: Map<EngineType, EngineAdapter> = new Map();
  private static instance: EngineAdapterFactoryImpl;

  private constructor() {
    // 注册内置适配器
    this.registerBuiltInAdapters();
  }

  /**
   * 获取工厂单例实例
   */
  static getInstance(): EngineAdapterFactoryImpl {
    if (!EngineAdapterFactoryImpl.instance) {
      EngineAdapterFactoryImpl.instance = new EngineAdapterFactoryImpl();
    }
    return EngineAdapterFactoryImpl.instance;
  }

  /**
   * 注册内置适配器
   */
  private registerBuiltInAdapters(): void {
    this.register(new OpenClawAdapter());
    this.register(new AICodingAdapter());
    this.register(new ClaudeCodeAdapter());
    this.register(new ApplicationCodingAdapter());
    this.register(new HermesAdapter());
    this.register(new TeclawAdapter());
  }

  /**
   * 获取指定引擎的适配器
   * @param engineType 引擎类型
   * @param context 可选的上下文信息，用于根据 templateType 返回不同的 adapter
   */
  getAdapter(
    engineType: EngineType,
    context?: { templateType?: string },
  ): EngineAdapter {
    // 如果传入了 context 且 templateType 是 applicationCoding，直接使用专门的 adapter
    if (context?.templateType === 'applicationCoding') {
      const appAdapter = this.adapters.get('applicationCoding' as EngineType);
      if (appAdapter) {
        return appAdapter;
      }
    }

    const adapter = this.adapters.get(engineType);
    if (!adapter) {
      // 如果没有找到适配器，返回 OpenClaw 作为默认回退
      console.warn(
        `[EngineAdapterFactory] 未找到引擎 "${engineType}" 的适配器，使用默认配置`,
      );
      return this.adapters.get('openclaw')!;
    }
    return adapter;
  }

  /**
   * 注册新的引擎适配器
   */
  register(adapter: EngineAdapter): void {
    this.adapters.set(adapter.engineType, adapter);
  }

  /**
   * 检查是否支持指定引擎
   */
  hasAdapter(engineType: EngineType): boolean {
    return this.adapters.has(engineType);
  }

  /**
   * 获取所有已注册的引擎类型
   */
  getAllEngineTypes(): EngineType[] {
    return Array.from(this.adapters.keys());
  }

  /**
   * 获取所有支持的引擎特性配置
   */
  getAllFeatures() {
    return Array.from(this.adapters.values()).map((adapter) =>
      adapter.getFeatures(),
    );
  }
}

// 导出单例实例
export const engineAdapterFactory = EngineAdapterFactoryImpl.getInstance();

/**
 * 便捷的 Hook 函数：获取指定引擎的适配器
 */
export function useEngineAdapter(engineType: EngineType): EngineAdapter {
  return engineAdapterFactory.getAdapter(engineType);
}

/**
 * 便捷的 Hook 函数：计算 Bot 的功能特性
 * 结合引擎类型和 Bot 上下文信息
 */
export function useBotFeatures(
  engineType: EngineType,
  context: FeatureContext,
): BotFeatures {
  const adapter = engineAdapterFactory.getAdapter(engineType);
  return adapter.computeFeatures(context);
}

/**
 * 便捷的 Hook 函数：检查特定功能是否支持
 */
export function useFeatureSupport(
  engineType: EngineType,
  feature: keyof BotFeatures,
  context?: FeatureContext,
): boolean {
  const adapter = engineAdapterFactory.getAdapter(engineType);
  return adapter.supports(feature, context);
}

/**
 * 便捷 Hook 函数：获取指定引擎的默认模型 ID
 */
export function useEngineDefaultModelId(engineType: EngineType): string | null {
  const adapter = engineAdapterFactory.getAdapter(engineType);
  return adapter.getDefaultModelId();
}

// 重新导出类型和适配器
export type { EngineAdapter, BotFeatures, FeatureContext };
export {
  OpenClawAdapter,
  AICodingAdapter,
  ClaudeCodeAdapter,
  HermesAdapter,
  TeclawAdapter,
};
