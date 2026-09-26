// CSS contract checks only; real geometry is verified in the local browser.
import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const records = readFileSync('web/pages/InsightCenter/monitoring/monitoring-records.css', 'utf8');
const pagination = readFileSync('web/pages/InsightCenter/monitoring/monitoring-pagination.css', 'utf8');

describe('Diagnosis summary layout contract', () => {
  it('truncates long TC labels on one line without covering the time column', () => {
    const blocks = [...records.matchAll(/\.insight-monitoring \.record-summary>\.summary-tc\s*\{([^}]+)\}/g)]
      .map(match => match[1]);
    expect(blocks.some(block => /white-space:\s*nowrap/.test(block) && /text-overflow:\s*ellipsis/.test(block) && /overflow:\s*hidden/.test(block))).toBe(true);
  });
  it('keeps compact TC text widths with sixteen pixels of extra separation', () => {
    expect(records).toMatch(/@media\(min-width:651px\)\s*\{[^}]+margin-right:16px/s);
    expect(records).toContain('minmax(0,1fr) 176px 170px 20px');
    expect(records).toContain('minmax(0,1fr) 156px 140px 15px');
    expect(pagination).not.toMatch(/summary-tc|record-summary|list-columns/);
  });
});
