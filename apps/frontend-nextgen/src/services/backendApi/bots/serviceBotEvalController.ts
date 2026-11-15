import { backendRequest } from '../httpClient';
import { isEnvelopeSuccess, type BackendUnknownRecord } from '../types';

export interface ServiceBotEvalOpenApiResponse<T> extends BackendUnknownRecord {
  code: number;
  message?: string;
  data?: T;
  request_id?: string;
}

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
  archive_download_url?: string;
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

const request = async <T>(
  url: string,
  method = 'GET',
  params: BackendUnknownRecord = {},
  rawBody?: BodyInit,
) => {
  const envelope = await backendRequest<ServiceBotEvalOpenApiResponse<T>>(url, { method, params, rawBody });
  return {
    success: isEnvelopeSuccess(envelope),
    message: envelope.message,
    error_code: envelope.code,
    data: envelope.data,
  } satisfies ServiceBotEvalApiResponse<T>;
};

const botBasePath = (botId: string) =>
  `/openapi/v1/bots/${encodeURIComponent(botId)}/eval`;

const versionBasePath = (botId: string, botVersion: string) =>
  `${botBasePath(botId)}/versions/${encodeURIComponent(botVersion)}`;

export const serviceBotEvalController = {
  detail: (botId: string, botVersion: string, ownerId?: string) =>
    request<ServiceBotEvalDetailDto>(versionBasePath(botId, botVersion), 'GET', {
      owner_id: ownerId,
    }),
  precheck: (botId: string, botVersion: string, ownerId?: string) =>
    request<ServiceBotEvalGateResultDto>(
      `${versionBasePath(botId, botVersion)}/simple-cert/precheck`,
      'POST',
      { owner_id: ownerId },
    ),
  sync: (botId: string, botVersion: string, ownerId?: string) =>
    request<ServiceBotEvalGateResultDto>(
      `${versionBasePath(botId, botVersion)}/simple-cert/sync`,
      'POST',
      { owner_id: ownerId },
    ),
  history: (botId: string, botVersion: string, ownerId?: string, pageNo = 1, pageSize = 10) =>
    request<ServiceBotEvalHistoryDto>(
      `${versionBasePath(botId, botVersion)}/history`,
      'GET',
      {
        owner_id: ownerId,
        page_no: pageNo,
        page_size: pageSize,
      },
    ),
  submission: (botId: string, submissionId: string, ownerId?: string) =>
    request<ServiceBotEvalSubmissionDto>(
      `${botBasePath(botId)}/submissions/${encodeURIComponent(submissionId)}`,
      'GET',
      { owner_id: ownerId },
    ),
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
    return request<ServiceBotEvalSubmissionDto>(
      `${versionBasePath(botId, botVersion)}/simple-cert/submissions`,
      'POST',
      { owner_id: ownerId },
      body,
    );
  },
};
