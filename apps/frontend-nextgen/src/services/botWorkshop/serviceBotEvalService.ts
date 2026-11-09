import {
  serviceBotEvalController,
  type ServiceBotEvalDetailDto,
  type ServiceBotEvalGateResultDto,
  type ServiceBotEvalHistoryDto,
  type ServiceBotEvalSubmissionDto,
} from '@/services/backendApi/bots/serviceBotEvalController';

export type { ServiceBotEvalHistoryDto, ServiceBotEvalSubmissionDto } from '@/services/backendApi/bots/serviceBotEvalController';

function assertSuccess<T>(response: { success: boolean; message?: string; data?: T }) {
  if (!response.success) throw new Error(response.message || '评测服务返回失败');
  return response.data;
}

export type ServiceBotEvalTriggerSource = 'manual_simple_cert' | 'manual_restart_eval';

export const serviceBotEvalService = {
  precheck: async (botId: string, botVersion: string, ownerId?: string) =>
    assertSuccess(await serviceBotEvalController.precheck(botId, botVersion, ownerId)) as ServiceBotEvalGateResultDto,
  detail: async (botId: string, botVersion: string, ownerId?: string) =>
    assertSuccess(await serviceBotEvalController.detail(botId, botVersion, ownerId)) as ServiceBotEvalDetailDto,
  sync: async (botId: string, botVersion: string, ownerId?: string) =>
    assertSuccess(await serviceBotEvalController.sync(botId, botVersion, ownerId)) as ServiceBotEvalGateResultDto,
  history: async (
    botId: string,
    botVersion: string,
    ownerId?: string,
    pageNo = 1,
    pageSize = 10,
  ) => assertSuccess(await serviceBotEvalController.history(botId, botVersion, ownerId, pageNo, pageSize)) as ServiceBotEvalHistoryDto,
  submission: async (botId: string, submissionId: string, ownerId?: string) =>
    assertSuccess(await serviceBotEvalController.submission(botId, submissionId, ownerId)) as ServiceBotEvalSubmissionDto,
  submit: async (
    botId: string,
    botVersion: string,
    ownerId: string | undefined,
    sampleFiles: File[],
    reportFiles: File[],
    triggerSource: ServiceBotEvalTriggerSource,
  ) => assertSuccess(
    await serviceBotEvalController.submit(botId, botVersion, ownerId, sampleFiles, reportFiles, triggerSource),
  ) as ServiceBotEvalSubmissionDto,
};
