/** Wire DTOs for the existing Bot engine version and runtime endpoints. */
export type BotEngineVersionDetectionStatusDto = 'known' | 'legacy' | 'read_failed' | 'not_applicable';
export type BotEngineVersionComparisonDto = 'older' | 'same' | 'newer' | 'legacy' | 'unknown';
export type BotEngineRestartHintDto = 'recommended' | 'restricted' | 'none' | 'unknown';

export interface BotEngineVersionDto {
  detection: { status: BotEngineVersionDetectionStatusDto; reason?: string };
  current?: { release_id?: string; raw_version?: string; source?: 'engine_env' };
  release_target: {
    status: 'available' | 'unavailable';
    release_id?: string;
    source?: 'backend_image_env';
    reason?: string;
  };
  comparison: BotEngineVersionComparisonDto;
  restart_hint: BotEngineRestartHintDto;
  restart_reason?: string;
  context: { bot_id: string; stage: string; checked_at: string; instance_id?: string };
}

export interface BotEngineRuntimeStatusDto {
  engine: string;
  active_connections: number;
  running: boolean;
}

export interface BotEngineRestartResultDto {
  bot_id: string;
  status: string;
}

export interface BotEngineRuntimeRequestOptions {
  userId: string;
  ownerId?: string;
  deviceUuid?: string;
  stage?: 'draft';
  signal?: AbortSignal;
}
