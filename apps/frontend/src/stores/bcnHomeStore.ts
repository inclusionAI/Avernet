/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 *
 * BCN Home Store - 产品首页状态（纯数据，无副作用）
 *
 * 仅承载首页接入指令所需的注册 token 状态；不进 stores/index.ts barrel，
 * BCN 闭包按需从 '@/stores/bcnHomeStore' 具名导入（de-barrel 纪律）。
 */

import { create } from 'zustand';
import { devtools } from 'zustand/middleware';

interface BcnHomeState {
  /** 当前用户的 Bot 注册 token；null = 未获取 / 未登录 */
  registerToken: string | null;
  /** token 过期时间（毫秒时间戳）；null = 未获取 */
  expiresAt: number | null;
  /** token 拉取中 */
  isLoadingToken: boolean;

  setToken: (token: string | null, expiresAt: number | null) => void;
  setLoadingToken: (loading: boolean) => void;
  reset: () => void;
}

const initialState = {
  registerToken: null as string | null,
  expiresAt: null as number | null,
  isLoadingToken: false,
};

export const useBcnHomeStore = create<BcnHomeState>()(
  devtools(
    (set) => ({
      ...initialState,

      setToken: (registerToken, expiresAt) =>
        set({ registerToken, expiresAt }, false, 'bcnHome/setToken'),

      setLoadingToken: (isLoadingToken) =>
        set({ isLoadingToken }, false, 'bcnHome/setLoadingToken'),

      reset: () => set(initialState, false, 'bcnHome/reset'),
    }),
    { name: 'BcnHomeStore' },
  ),
);
