import { userScopedParams } from '@/services/backendApi/bots/botController';
import { backendRequest } from '@/services/backendApi/httpClient';
import { getSampleSetErrorMessage, isSampleSetNotFoundError } from '@/services/botWorkshop/standardEvaluationErrorPolicy';
import { useErrorNotifyStore } from '@/stores/errorNotifyStore';
import { isEnvelopeSuccess, type BackendUnknownRecord } from '@/services/backendApi/types';

function readToastKey(error: unknown): string | undefined {
  if (error === null || error === undefined) return undefined;
  const toastKey = (error as { toastKey?: unknown }).toastKey;
  return typeof toastKey === 'string' && toastKey ? toastKey : undefined;
}

export type StandardEvaluationGoalType = 'json' | 'semantic';

export interface StandardEvaluationSetItem extends BackendUnknownRecord {
  uuid: string;
  name: string;
  description?: string;
  creator?: string;
  sample_count: number;
  sample_set_url?: string;
}

export interface StandardEvaluationSetListResult extends BackendUnknownRecord {
  support: boolean;
  sets: StandardEvaluationSetItem[];
}

export interface StandardEnvironment extends BackendUnknownRecord {
  supported?: boolean;
  default_tag?: string;
  build_status: string;
  build_info?: string;
  error?: string;
}

export interface StandardEnvironmentDestroyResult extends BackendUnknownRecord {
  supported?: boolean;
  default_tag?: string;
  destroyed: boolean;
  destroy_status?: string;
  reason?: string;
  error?: string;
}

export interface StandardQualityTaskExt extends BackendUnknownRecord {
  publish_id?: number;
  version?: number;
  set_uuid?: string;
  set_task_uuid?: string;
  eval_progress?: StandardEvaluationProgress;
  error?: string;
  error_msg?: string;
  sample_set_name?: string;
  sample_set_count?: number;
}

export interface StandardEvaluationProgress extends BackendUnknownRecord {
  set_task_uuid?: string;
  status?: string;
  status_label?: string;
  version?: string;
  executed_count?: number;
  total_count?: number;
  report?: {
    uuid?: string;
    reportUrl?: string;
    content_md?: string;
  };
  gmt_create?: string;
  gmt_modified?: string;
}

export interface StandardTaskEnvironment extends StandardEnvironment {
  supported?: boolean;
  destroy_status?: string;
}

export interface StandardQualityTask extends BackendUnknownRecord {
  id: number;
  task_id?: number;
  uuid?: string;
  task_type?: string;
  biz_type?: string;
  status: string;
  bot_id?: string;
  owner_id?: string;
  ext?: StandardQualityTaskExt;
  environment?: StandardTaskEnvironment;
  environment_status?: string;
  set_uuid?: string;
  set_name?: string;
  sample_count?: number;
  set_task_uuid?: string;
  progress?: StandardEvaluationProgress;
  progress_status?: string;
  report_url?: string;
  error?: string;
  steps?: StandardEvaluationTaskStep[];
}

export interface StandardEvaluationOpenApiResponse<T> extends BackendUnknownRecord {
  code?: number;
  message?: string;
  data?: T;
  request_id?: string;
}

export type StandardEvaluationStepStatus = 'WAITING' | 'RUNNING' | 'DONE' | 'FAILED' | 'UNKNOWN';

export interface StandardEvaluationTaskStep extends BackendUnknownRecord {
  key: string;
  status: StandardEvaluationStepStatus;
}

export const standardEvaluationService = {
  async listEvaluationSets(
    botId: string,
    botVersion: string,
    ownerId?: string,
  ): Promise<StandardEvaluationSetListResult> {
    try {
      const response = await backendRequest<StandardEvaluationOpenApiResponse<StandardEvaluationSetListResult>>(
        `/openapi/v1/bots/${encodeURIComponent(botId)}/eval/versions/${encodeURIComponent(
          botVersion,
        )}/sample-sets`,
        { method: 'GET', params: userScopedParams({ owner_id: ownerId }) },
      );
      if (!isEnvelopeSuccess(response) || !response.data) {
        throw new Error(response.message || '获取评测样本集失败');
      }
      return response.data;
    } catch (error) {
      if (isSampleSetNotFoundError(error)) {
        const message = getSampleSetErrorMessage(error);
        const toastKey = readToastKey(error);
        if (toastKey) {
          useErrorNotifyStore.getState().cancel(toastKey);
        }
        throw new Error(message);
      }
      throw error;
    }
  },

  async createStandardEvaluationTask(input: {
    botId: string;
    botVersion: string;
    ownerId?: string;
    setUuid: string;
    goalType: StandardEvaluationGoalType;
    goalJson?: Record<string, unknown> | null;
    goalText?: string;
    triggerSource?: string;
  }): Promise<StandardQualityTask> {
    const response = await backendRequest<StandardEvaluationOpenApiResponse<StandardQualityTask>>(
      `/openapi/v1/bots/${encodeURIComponent(input.botId)}/eval/versions/${encodeURIComponent(
        input.botVersion,
      )}/tasks`,
      {
        method: 'POST',
        params: userScopedParams({ owner_id: input.ownerId }),
        data: {
          set_uuid: input.setUuid,
          goal_type: input.goalType,
          goal_json: input.goalJson || undefined,
          goal_text: input.goalText || '',
          trigger_source: input.triggerSource || 'manual_restart_eval',
        },
      },
    );
    if (!isEnvelopeSuccess(response) || !response.data) {
      throw new Error(response.message || '创建标准评测任务失败');
    }
    return response.data;
  },

  async getStandardEvaluationCurrentTask(input: {
    botId: string;
    botVersion: string;
    ownerId?: string;
  }): Promise<StandardQualityTask> {
    const response = await backendRequest<StandardEvaluationOpenApiResponse<StandardQualityTask>>(
      `/openapi/v1/bots/${encodeURIComponent(input.botId)}/eval/versions/${encodeURIComponent(
        input.botVersion,
      )}/tasks/current`,
      { method: 'GET', params: userScopedParams({ owner_id: input.ownerId }) },
    );
    if (!isEnvelopeSuccess(response) || !response.data) {
      throw new Error(response.message || '获取标准评测任务状态失败');
    }
    return response.data;
  },

  async createStandardEnvironment(input: {
    botId: string;
    botVersion: string;
    ownerId?: string;
    defaultTag?: string;
    ttlMinutes?: number;
  }): Promise<StandardEnvironment> {
    const response = await backendRequest<StandardEvaluationOpenApiResponse<StandardEnvironment>>(
      `/openapi/v1/bots/${encodeURIComponent(input.botId)}/eval/versions/${encodeURIComponent(
        input.botVersion,
      )}/environments`,
      {
        method: 'POST',
        params: userScopedParams({ owner_id: input.ownerId }),
        data: {
          default_tag: input.defaultTag || 'eval',
          ttl_minutes: input.ttlMinutes || 120,
        },
      },
    );
    if (!isEnvelopeSuccess(response) || !response.data) {
      throw new Error(response.message || '创建评测环境失败');
    }
    return response.data;
  },

  async getStandardEnvironmentCurrent(input: {
    botId: string;
    botVersion: string;
    ownerId?: string;
  }): Promise<StandardEnvironment> {
    const response = await backendRequest<StandardEvaluationOpenApiResponse<StandardEnvironment>>(
      `/openapi/v1/bots/${encodeURIComponent(input.botId)}/eval/versions/${encodeURIComponent(
        input.botVersion,
      )}/environments/current`,
      { method: 'GET', params: userScopedParams({ owner_id: input.ownerId }) },
    );
    if (!isEnvelopeSuccess(response) || !response.data) {
      throw new Error(response.message || '获取评测环境状态失败');
    }
    return response.data;
  },

  async destroyStandardEnvironment(input: {
    botId: string;
    botVersion: string;
    ownerId?: string;
    reason?: string;
  }): Promise<StandardEnvironmentDestroyResult> {
    const response = await backendRequest<
      StandardEvaluationOpenApiResponse<StandardEnvironmentDestroyResult>
    >(
      `/openapi/v1/bots/${encodeURIComponent(input.botId)}/eval/versions/${encodeURIComponent(
        input.botVersion,
      )}/environments/current`,
      {
        method: 'DELETE',
        params: userScopedParams({
          owner_id: input.ownerId,
          reason: input.reason || 'standard_eval_finished',
        }),
      },
    );
    if (!isEnvelopeSuccess(response) || !response.data) {
      throw new Error(response.message || '销毁评测环境失败');
    }
    return response.data;
  },
};
