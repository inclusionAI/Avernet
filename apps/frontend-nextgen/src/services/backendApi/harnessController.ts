import { backendRequest } from './httpClient';

export interface OpenApiEnvelope<T> {
  code: number;
  message: string;
  data: T | null;
  request_id: string;
}

export interface HarnessDiagnoseRequestDto {
  entity_type: string;
  entity_id: string;
  scan_type?: string;
  layer?: string;
  bot_publish_id?: string;
}

export interface HarnessDiagnoseStartResponseDto {
  success?: boolean;
  scan_id: number;
  bot_id: string;
  entity_id: string;
  status: string;
  message?: string;
}

export interface HarnessPatchOperationDto {
  op?: string;
  target?: string;
  template?: string | null;
  detail?: Record<string, unknown>;
}

export interface HarnessPatchItemDto {
  patch_id?: number;
  name?: string;
  description?: string | null;
  is_applied?: boolean;
  layer?: string;
  operations?: HarnessPatchOperationDto[];
  is_patch?: boolean;
  advise?: string | null;
  is_advise?: boolean;
  gmt_create?: string | null;
}

export interface HarnessDimReportItemDto {
  scan_dim?: string | null;
  health_score?: number | null;
  grade?: string | null;
  check_items?: unknown;
  findings?: unknown;
  findings_summary?: Record<string, number> | string | null;
  trigger_source?: string | null;
  status?: string | null;
  failed_reason?: string | null;
  env?: string | null;
  duration_ms?: number | null;
  scan_type?: string | null;
  scan_report_type?: string | null;
  patch_ids?: unknown;
  patches?: HarnessPatchItemDto[];
  gmt_create?: string | null;
}

export interface HarnessDimReportResponseDto {
  bot_id: string;
  entity_id: string;
  bot_publish_id?: string | null;
  items: HarnessDimReportItemDto[];
}

export interface HarnessDimHistoryRecordItemDto extends HarnessDimReportItemDto {
  id?: number;
  bot_id?: string;
  entity_id?: string;
  bot_publish_id?: string | null;
  gmt_modified?: string | null;
}

export interface HarnessDimHistoryResponseDto {
  bot_id: string;
  entity_id: string;
  scan_dim?: string | null;
  bot_publish_id?: string | null;
  total: number;
  page: number;
  size: number;
  items: HarnessDimHistoryRecordItemDto[];
}

const BASE = '/openapi/v1/bots';

function assertData<T>(envelope: OpenApiEnvelope<T>, label: string): T {
  if (!envelope || envelope.data === null || envelope.data === undefined) {
    throw new Error(`${label} 返回为空`);
  }
  return envelope.data;
}

function botHarnessBase(botId: string) {
  return `${BASE}/${encodeURIComponent(botId)}/harness`;
}

export async function startHarnessDiagnose(
  botId: string,
  userId: string,
  data: HarnessDiagnoseRequestDto,
): Promise<HarnessDiagnoseStartResponseDto> {
  const envelope = await backendRequest<OpenApiEnvelope<HarnessDiagnoseStartResponseDto>>(
    `${botHarnessBase(botId)}/diagnose`,
    { method: 'POST', params: { user_id: userId }, data },
  );
  return assertData(envelope, '健康检查');
}

export async function getHarnessDimReport(params: {
  botId: string;
  userId: string;
  entityId: string;
  botPublishId?: string;
}): Promise<HarnessDimReportResponseDto> {
  const envelope = await backendRequest<OpenApiEnvelope<HarnessDimReportResponseDto>>(
    `${botHarnessBase(params.botId)}/dim-report`,
    {
      method: 'GET',
      params: {
        user_id: params.userId,
        entity_id: params.entityId,
        bot_publish_id: params.botPublishId,
      },
      retryOnTransient: true,
    },
  );
  return assertData(envelope, '健康报告');
}

export async function getHarnessDimHistory(params: {
  botId: string;
  userId: string;
  entityId: string;
  botPublishId?: string;
  page?: number;
  size?: number;
  scanDim?: string;
}): Promise<HarnessDimHistoryResponseDto> {
  const envelope = await backendRequest<OpenApiEnvelope<HarnessDimHistoryResponseDto>>(
    `${botHarnessBase(params.botId)}/dim-history`,
    {
      method: 'GET',
      params: {
        user_id: params.userId,
        entity_id: params.entityId,
        bot_publish_id: params.botPublishId,
        page: params.page ?? 1,
        size: params.size ?? 20,
        scan_dim: params.scanDim,
      },
      retryOnTransient: true,
    },
  );
  return assertData(envelope, '健康历史');
}
