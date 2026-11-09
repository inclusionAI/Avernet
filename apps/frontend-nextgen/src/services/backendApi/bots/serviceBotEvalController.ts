import { backendRequest } from '../httpClient';
import type { BackendUnknownRecord } from '../types';

export interface ServiceBotEvalApiResponse<T> extends BackendUnknownRecord {
  success: boolean;
  message?: string;
  error_code?: number;
  data?: T;
}

export interface ServiceBotEvalFileDto extends BackendUnknownRecord {
  filename: string;
  size: number;
  content_type?: string;
}

export interface ServiceBotEvalGateResultDto extends BackendUnknownRecord {
  gate_status: string;
  gate_result: string;
  allowed: boolean;
  reason?: string;
  display_message?: string;
  next_action?: string;
  redirect_url?: string;
}

export interface ServiceBotEvalDetailDto extends BackendUnknownRecord {
  bot_id: string;
  bot_version: string;
  owner_permission?: { operable: boolean; reason?: string };
  eval_scope?: BackendUnknownRecord;
  tabs?: {
    simple_cert_submit?: { enabled?: boolean; visible?: boolean };
    restart_eval?: { enabled?: boolean; visible?: boolean; visible_reason?: string };
    eval_history?: { enabled?: boolean; visible?: boolean };
  };
  cert_state?: BackendUnknownRecord;
}

export interface ServiceBotEvalSubmissionDto extends BackendUnknownRecord {
  submission_id: string;
  task_type: string;
  task_status: string;
  next_step?: string;
  message?: string;
  archive_download_url?: string;
}

export interface ServiceBotEvalHistoryRecord extends BackendUnknownRecord {
  id?: string;
  record_id?: string;
  mode?: string;
  status?: string;
  summary?: string;
  report_url?: string;
  evaluated_at?: string;
}

export interface ServiceBotEvalHistoryDto extends BackendUnknownRecord {
  records: ServiceBotEvalHistoryRecord[];
  page: {
    page_no: number;
    page_size: number;
    total: number;
  };
}

const request = <T>(url: string, method = 'GET', params: BackendUnknownRecord = {}) =>
  backendRequest<ServiceBotEvalApiResponse<T>>(url, { method, params });

const basePath = (botId: string, botVersion: string, action: string) =>
  `/api/service-bot/eval/${encodeURIComponent(botId)}/versions/${encodeURIComponent(botVersion)}/${action}`;

export const serviceBotEvalController = {
  detail: (botId: string, botVersion: string, ownerId?: string) =>
    request<ServiceBotEvalDetailDto>(basePath(botId, botVersion, 'detail'), 'GET', {
      owner_id: ownerId,
    }),
  precheck: (botId: string, botVersion: string, ownerId?: string) =>
    request<ServiceBotEvalGateResultDto>(basePath(botId, botVersion, 'simple-cert/precheck'), 'POST', {
      owner_id: ownerId,
    }),
  sync: (botId: string, botVersion: string, ownerId?: string) =>
    request<ServiceBotEvalGateResultDto>(basePath(botId, botVersion, 'simple-cert/sync'), 'POST', {
      owner_id: ownerId,
    }),
  history: (botId: string, botVersion: string, ownerId?: string, pageNo = 1, pageSize = 10) =>
    request<ServiceBotEvalHistoryDto>(basePath(botId, botVersion, 'history'), 'GET', {
      owner_id: ownerId,
      page_no: pageNo,
      page_size: pageSize,
    }),
  submission: (botId: string, submissionId: string, ownerId?: string) =>
    request<ServiceBotEvalSubmissionDto>('/api/service-bot/eval/submissions/' + encodeURIComponent(submissionId), 'GET', {
      bot_id: botId,
      owner_id: ownerId,
    }),
  submit: (
    botId: string,
    botVersion: string,
    ownerId: string | undefined,
    sampleFiles: File[],
    reportFiles: File[],
    triggerSource: string,
  ) => {
    const body = new FormData();
    sampleFiles.forEach((file) => body.append('sample_files', file, file.name));
    reportFiles.forEach((file) => body.append('report_files', file, file.name));
    body.append('trigger_source', triggerSource);
    return backendRequest<ServiceBotEvalApiResponse<ServiceBotEvalSubmissionDto>>(
      basePath(botId, botVersion, 'simple-cert/submissions'),
      { method: 'POST', params: { owner_id: ownerId }, rawBody: body },
    );
  },
};
