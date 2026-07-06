/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 *
 * Model Store - 模型状态管理
 */

import { engineAdapterFactory } from '@/adapters/engine';
import type { EngineType } from '@/services/backend-api/BotController';
import { Model } from '@/services/backend-api/ModelController';
import { create } from 'zustand';
import { devtools } from 'zustand/middleware';

interface ModelState {
  // State
  models: Model[];
  activeModel: Model | null;
  isLoading: boolean;

  // Actions
  setModels: (models: Model[]) => void;
  setActiveModel: (model: Model | null) => void;
  setLoading: (loading: boolean) => void;
  reset: () => void;

  // Computed
  getModelById: (id: string) => Model | undefined;
  getDefaultModel: () => Model | undefined;
  /** 根据引擎类型获取默认模型 */
  getDefaultModelByEngine: (engineType: EngineType) => Model | undefined;
  getEnterpriseModels: () => Model[];
}

const initialState = {
  models: [],
  activeModel: null,
  isLoading: false,
};

export const useModelStore = create<ModelState>()(
  devtools(
    (set, get) => ({
      // Initial State
      ...initialState,

      // Actions
      setModels: (models) => set({ models }, false, 'setModels'),

      setActiveModel: (activeModel) =>
        set({ activeModel }, false, 'setActiveModel'),

      setLoading: (isLoading) => set({ isLoading }, false, 'setLoading'),

      reset: () => set(initialState, false, 'reset'),

      // Computed Getters
      getModelById: (id) => {
        return get().models.find((m) => m.id === id);
      },

      getDefaultModel: () => {
        // 优先返回企业默认模型
        return (
          get().models.find((m) => m.enterprise_default) || get().models[0]
        );
      },

      /**
       * 根据引擎类型获取默认模型
       * 1. 如果引擎适配器配置了 defaultModelId，优先使用
       * 2. 否则使用全局默认（enterprise_default 或第一个模型）
       */
      getDefaultModelByEngine: (engineType: EngineType) => {
        const { models } = get();
        if (models.length === 0) return undefined;

        // 获取引擎适配器配置的默认模型 ID
        const adapter = engineAdapterFactory.getAdapter(engineType);
        const engineDefaultModelId = adapter.getDefaultModelId();

        // 如果适配器指定了默认模型，优先使用
        if (engineDefaultModelId) {
          const engineDefaultModel = models.find(
            (m) => m.id === engineDefaultModelId,
          );
          if (engineDefaultModel) {
            return engineDefaultModel;
          }
          console.warn(
            `[ModelStore] 引擎 ${engineType} 配置的默认模型 ${engineDefaultModelId} 不存在，回退到全局默认`,
          );
        }

        // 回退到全局默认
        return models.find((m) => m.enterprise_default) || models[0];
      },

      getEnterpriseModels: () => {
        return get().models.filter((m) => m.enterprise_enabled);
      },
    }),
    { name: 'ModelStore' },
  ),
);
