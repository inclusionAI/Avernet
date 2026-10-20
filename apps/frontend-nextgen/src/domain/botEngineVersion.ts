import type { BotRuntimeStage } from '@/domain/botWorkshop';

export type EngineVersionDetectionStatus = 'known' | 'legacy' | 'read_failed' | 'not_applicable';
export type EngineVersionComparison = 'older' | 'same' | 'newer' | 'legacy' | 'unknown';
export type EngineRestartHint = 'recommended' | 'restricted' | 'none' | 'unknown';

export interface EngineVersionDetection {
  status: EngineVersionDetectionStatus;
  reason?: string;
}

export interface EngineCurrentVersion {
  release_id?: string;
  raw_version?: string;
  source?: 'engine_env';
}

export interface EngineReleaseTarget {
  status: 'available' | 'unavailable';
  release_id?: string;
  source?: 'backend_image_env';
  reason?: string;
}

export interface EngineVersionContext {
  bot_id: string;
  stage: string;
  checked_at: string;
  instance_id?: string;
}

/** Domain representation of the read-only engine version observation. */
export interface EngineVersionObservation {
  detection: EngineVersionDetection;
  current?: EngineCurrentVersion;
  release_target: EngineReleaseTarget;
  comparison: EngineVersionComparison;
  restart_hint: EngineRestartHint;
  restart_reason?: string;
  context: EngineVersionContext;
}

export interface EngineRestartPermissionInput {
  actions: string[];
  disabledActions?: Record<string, string>;
  lockedByOther?: boolean;
  serviceBot?: boolean;
  runtimeStage?: BotRuntimeStage;
}

export interface EngineVersionUpdateAvailability {
  enabled: boolean;
  disabledReason?: string;
}

export interface EngineVersionTarget {
  key: string;
  botId: string;
  userId: string;
  ownerId: string;
  engine: string;
  enabled: boolean;
  restartPermission: EngineRestartPermissionInput;
}

export type EngineVersionUiState = 'updatable' | 'no_update' | 'unknown' | 'not_applicable';
const SUPPORTED_ENGINE_VERSION_ENGINES = new Set(['openclaw', 'hermes']);

export function supportsEngineVersion(engine: string): boolean {
  return SUPPORTED_ENGINE_VERSION_ENGINES.has(engine.trim().toLowerCase());
}

export function engineVersionUiState(observation: EngineVersionObservation): EngineVersionUiState {
  if (observation.detection.status === 'not_applicable') return 'not_applicable';
  if (observation.detection.status === 'read_failed' || observation.release_target.status !== 'available') {
    return 'unknown';
  }
  if (
    (observation.comparison === 'older' || observation.comparison === 'legacy') &&
    observation.restart_hint === 'recommended'
  ) {
    return 'updatable';
  }
  if (
    (observation.comparison === 'same' || observation.comparison === 'newer') &&
    (observation.restart_hint === 'none' || observation.restart_hint === 'restricted')
  )
    return 'no_update';
  return 'unknown';
}

export function isEngineVersionUpdated(observation: EngineVersionObservation): boolean {
  return (
    observation.detection.status === 'known' &&
    observation.release_target.status === 'available' &&
    (observation.comparison === 'same' || observation.comparison === 'newer') &&
    (observation.restart_hint === 'none' || observation.restart_hint === 'restricted')
  );
}

function versionValue(value?: EngineCurrentVersion): string {
  return value?.raw_version?.trim() || value?.release_id?.trim() || '';
}

export function currentEngineVersionLabel(observation: EngineVersionObservation): string {
  const version = versionValue(observation.current);
  if (version) return version;
  return observation.detection.status === 'legacy' ? '未标记' : '未提供';
}

export function targetEngineVersionLabel(observation: EngineVersionObservation): string {
  if (observation.release_target.status !== 'available') return '未配置';
  return observation.release_target.release_id?.trim() || '未提供';
}

export function engineVersionTooltip(observation: EngineVersionObservation): string {
  return `当前版本：${currentEngineVersionLabel(observation)}；目标版本：${targetEngineVersionLabel(observation)}`;
}

export function engineVersionNoUpdateMessage(observation: EngineVersionObservation): string {
  return observation.comparison === 'newer' ? '当前版本高于目标版本，无需更新。' : '当前实例已是最新版本。';
}
