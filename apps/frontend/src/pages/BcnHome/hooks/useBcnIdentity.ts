/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 *
 * useBcnIdentity - 产品首页顶栏身份 Hook
 *
 * 拉取当前用户花名（GET /bcnproxy/me）供顶栏头像/花名展示。
 * 未登录（401）或异常时静默降级：nickName 置 null，由 UI 显示占位头像、不显示名字
 * （首页未登录是正常态，不弹 toast）。
 */

import * as BcnController from '@/services/backend-api/BcnController';
import { retryOnTransient } from '@/utils/retryRequest';
import { useCallback, useEffect, useState } from 'react';

export function useBcnIdentity() {
  const [nickName, setNickName] = useState<string | null>(null);

  const fetchIdentity = useCallback(async () => {
    try {
      // 网关瞬时 504/网络抖动重试（顶栏花名非关键，间歇失败重试通常即成功）
      const res = await retryOnTransient(() => BcnController.getMe());
      setNickName(res?.nick_name ?? null);
    } catch (error) {
      // 401 未登录是正常态：清空花名，UI 显示占位头像
      console.error('[useBcnIdentity] 获取用户花名失败:', error);
      setNickName(null);
    }
  }, []);

  useEffect(() => {
    fetchIdentity();
  }, [fetchIdentity]);

  return { nickName };
}
