/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 *
 * useRegisterToken - 产品首页接入指令的注册 token Hook
 *
 * 拉取当前用户的 Bot 注册 token（GET /bcnproxy/register/token），写入 bcnHomeStore，
 * 供接入指令模板动态注入。未登录（401）时 token 置 null，由 UI 显示占位 + 登录提示，
 * 不弹 toast（首页未登录是正常态）。
 */

import * as BcnController from '@/services/backend-api/BcnController';
import { useBcnHomeStore } from '@/stores/bcnHomeStore';
import { retryOnTransient } from '@/utils/retryRequest';
import { useCallback } from 'react';

export function useRegisterToken() {
  const token = useBcnHomeStore((state) => state.registerToken);
  const expiresAt = useBcnHomeStore((state) => state.expiresAt);
  const isLoading = useBcnHomeStore((state) => state.isLoadingToken);
  const setToken = useBcnHomeStore((state) => state.setToken);
  const setLoadingToken = useBcnHomeStore((state) => state.setLoadingToken);

  const fetchToken = useCallback(async () => {
    setLoadingToken(true);
    try {
      // 网关瞬时 504/网络抖动重试（首页接入指令非关键，间歇失败重试通常即成功）
      const res = await retryOnTransient(() =>
        BcnController.getRegisterToken(),
      );
      setToken(res?.token ?? null, res?.expires_at ?? null);
    } catch (error) {
      // 401 未登录是正常态：清空 token，UI 显示占位
      console.error('[useRegisterToken] 获取注册 token 失败:', error);
      setToken(null, null);
    } finally {
      setLoadingToken(false);
    }
  }, [setToken, setLoadingToken]);

  return { token, expiresAt, isLoading, fetchToken };
}
