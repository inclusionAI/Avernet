/**
 * useActor - Actor 业务逻辑 Hook
 *
 * 封装 Actor 相关的业务逻辑和 API 调用
 * 包括：Actor 协作状态切换、参与者模式更新等
 *
 * 遵循三层架构：Hook 层封装业务逻辑、API 调用、错误处理和 Toast
 */

import * as BcnController from '@/services/backend-api/BcnController';
import {
  handleApiError,
  handleNetworkError,
  showSuccessToast,
} from '@/utils/hooksErrorHandler';
import { useCallback } from 'react';

/**
 * Actor 管理 Hook
 * 封装所有 Actor 相关的业务逻辑和 API 调用
 */
export function useActor() {
  /**
   * 更新 Actor 协作状态
   * @param actor_id Actor ID
   * @param_status 目标状态：'online' | 'hidden'
   * @returns 是否成功
   */
  const updateActorStatus = useCallback(
    async (actor_id: string, status: 'online' | 'hidden'): Promise<boolean> => {
      try {
        const response = await BcnController.updateActorStatus(
          { actor_id, status },
          { skipErrorHandler: true },
        );

        if (response.success || response.data) {
          showSuccessToast(
            { module: 'BCN', action: '更新协作状态' },
            `协作状态${status === 'online' ? '已开启' : '已暂停'}`,
          );
          return true;
        } else {
          handleApiError(
            { success: false, error: response.error },
            { module: 'BCN', action: '更新状态' },
          );
          return false;
        }
      } catch (error) {
        console.error('[useActor] Failed to update actor status:', error);
        handleNetworkError(error, { module: 'BCN', action: '更新状态' });
        return false;
      }
    },
    [],
  );

  /**
   * 更新参与者协作姿态/发言模式
   * @param group_id 群组 ID
   * @param actor_id Actor ID
   * @param mode 目标模式：'present' | 'absent' | 'auto' | 'muted'
   * @returns 是否成功
   */
  const updateParticipantMode = useCallback(
    async (
      group_id: string,
      actor_id: string,
      mode: 'present' | 'absent' | 'auto' | 'muted',
    ): Promise<boolean> => {
      try {
        const response = await BcnController.updateParticipantMode(
          { group_id, actor_id, mode },
          { skipErrorHandler: true },
        );

        // 429文档：响应现在是 {success: true, data: {...}} 格式，也可能直接返回在顶层
        const success =
          response.success ||
          response.group_id ||
          (response.data && response.data.group_id);
        if (success) {
          showSuccessToast(
            { module: 'BCN', action: '更新协作模式' },
            '协作模式已更新',
          );
          return true;
        } else {
          handleApiError(
            { success: false, error: response.error },
            { module: 'BCN', action: '更新协作模式' },
          );
          return false;
        }
      } catch (error) {
        console.error('[useActor] Failed to update participant mode:', error);
        handleNetworkError(error, { module: 'BCN', action: '更新协作模式' });
        return false;
      }
    },
    [],
  );

  return {
    updateActorStatus,
    updateParticipantMode,
  };
}
