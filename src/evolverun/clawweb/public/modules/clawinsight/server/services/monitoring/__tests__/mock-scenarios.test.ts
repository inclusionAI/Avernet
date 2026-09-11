import { describe, expect, it } from 'vitest';
import { createMonitoringMockScenario } from '../mock-scenarios.js';

describe('browser acceptance fixtures', () => {
  it('covers pagination per engine, conclusions, intervention, dates, missing time and status states', () => {
    const scenario = createMonitoringMockScenario('run', Date.parse('2026-09-10T09:00:00Z'));
    for (const bot of scenario.bots.slice(0, 2)) {
      const events = scenario.events.filter(e => e.botId === bot.botId);
      expect(events).toHaveLength(30);
      expect(new Set(events.map(e => e.decision)).size).toBe(3);
      expect(new Set(events.map(e => e.humanIntervention)).size).toBe(2);
      expect(new Set(events.map(e => e.occurredAt?.slice(0, 10)).filter(Boolean)).size).toBe(3);
      expect(events.filter(e => e.occurredAt === null)).toHaveLength(1);
    }
    expect(new Set(scenario.events.map(e => e.eventId)).size).toBe(60);
    expect(scenario.checks[1].status).toBe('ERROR');
    expect(Date.parse(scenario.checks[0].checkedAt) - Date.parse(scenario.checks[2].checkedAt)).toBe(3600000);
    expect(scenario.bots[3].paused).toBe(true);
  });
});
