import {
  currentEngineVersionLabel,
  engineVersionNoUpdateMessage,
  engineVersionTooltip,
  engineVersionUiState,
  isEngineVersionUpdated,
  targetEngineVersionLabel,
  type EngineVersionObservation,
} from '@/domain/botEngineVersion';
import { describe, expect, test } from '@jest/globals';

function observation(overrides: Partial<EngineVersionObservation> = {}): EngineVersionObservation {
  return {
    detection: { status: 'known' },
    current: { release_id: 'REL-1', raw_version: '1.2.0', source: 'engine_env' },
    release_target: { status: 'available', release_id: 'REL-2', source: 'backend_image_env' },
    comparison: 'older',
    restart_hint: 'recommended',
    context: { bot_id: 'bot-1', stage: 'draft', checked_at: '2026-10-20T00:00:00Z', instance_id: 'device-1' },
    ...overrides,
  };
}

describe('engine version UI mapping', () => {
  test('shows an update only for a supported, older version with Backend recommendation', () => {
    expect(engineVersionUiState(observation())).toBe('updatable');
  });

  test('keeps legacy without a current version eligible and labels it as unmarked', () => {
    const value = observation({
      detection: { status: 'legacy' },
      current: undefined,
      comparison: 'legacy',
    });

    expect(engineVersionUiState(value)).toBe('updatable');
    expect(currentEngineVersionLabel(value)).toBe('未标记');
    expect(targetEngineVersionLabel(value)).toBe('REL-2');
  });

  test.each(['same', 'newer'] as const)('%s never offers a downgrade/update action', (comparison) => {
    expect(engineVersionUiState(observation({ comparison, restart_hint: 'none' }))).toBe('no_update');
  });

  test('does not describe a version newer than the target as latest', () => {
    expect(engineVersionNoUpdateMessage(observation({ comparison: 'newer', restart_hint: 'none' }))).toContain(
      '高于目标',
    );
  });

  test('does not treat an unknown restart hint as latest or as a confirmed update', () => {
    const uncertain = observation({ comparison: 'same', restart_hint: 'unknown' });
    expect(engineVersionUiState(uncertain)).toBe('unknown');
    expect(isEngineVersionUpdated(uncertain)).toBe(false);
  });

  test.each(['read_failed', 'known'] as const)('%s plus unknown comparison never looks updatable', (status) => {
    expect(
      engineVersionUiState(observation({ detection: { status }, comparison: 'unknown', restart_hint: 'unknown' })),
    ).toBe('unknown');
  });

  test('does not convert failed reads or unavailable targets into an update or a successful update result', () => {
    const readFailed = observation({
      detection: { status: 'read_failed', reason: 'probe_timeout' },
      comparison: 'older',
      restart_hint: 'recommended',
    });
    const noTarget = observation({
      release_target: { status: 'unavailable', reason: 'target_not_configured' },
    });

    expect(engineVersionUiState(readFailed)).toBe('unknown');
    expect(engineVersionUiState(noTarget)).toBe('unknown');
    expect(isEngineVersionUpdated(readFailed)).toBe(false);
  });

  test('hides not-applicable engines and only declares an update complete after a known reread', () => {
    const notApplicable = observation({
      detection: { status: 'not_applicable', reason: 'engine_not_supported' },
    });
    const reread = observation({ comparison: 'same', restart_hint: 'none' });

    expect(engineVersionUiState(notApplicable)).toBe('not_applicable');
    expect(isEngineVersionUpdated(reread)).toBe(true);
    expect(engineVersionTooltip(observation({ current: undefined, detection: { status: 'legacy' } }))).toBe(
      '当前版本：未标记；目标版本：REL-2',
    );
  });
});
