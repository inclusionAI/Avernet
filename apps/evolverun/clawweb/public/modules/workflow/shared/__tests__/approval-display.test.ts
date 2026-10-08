import { describe, expect, it } from 'vitest';
import { parseApprovalCardContent, approvalDisplay } from '../approval-display.js';

describe('approval card content compatibility', () => {
  it('preserves old field arrays and section envelopes', () => {
    const fields = [{ label: '任务', value: 'T-1' }];
    expect(parseApprovalCardContent(JSON.stringify(fields)).fields).toEqual(fields);
    expect(parseApprovalCardContent(JSON.stringify({ fields, sections: [{ id: 'review', fields: [] }] })).sections).toHaveLength(1);
  });
  it('reads display-only envelopes without requiring sections', () => {
    const result = parseApprovalCardContent(JSON.stringify({ fields: [{ label: '任务', value: 'T-1' }], display: { confirmLabel: '执行处置', footer: '' } }));
    expect(result.fields).toHaveLength(1);
    expect(approvalDisplay('HUMAN_CONFIRM', result.display)).toMatchObject({ confirmLabel: '执行处置', rejectLabel: '拒绝', footer: '' });
  });
  it('retains defaults for absent or malformed data', () => {
    expect(parseApprovalCardContent('{invalid')).toEqual({ fields: [] });
    expect(approvalDisplay('HUMAN_CONFIRM')).toMatchObject({ confirmLabel: '确认执行', notePlaceholder: '备注（可选）' });
    expect(approvalDisplay('HUMAN_CONFIRM', { confirmLabel: 12 } as any).confirmLabel).toBe('确认执行');
  });
});


it('distinguishes zero duration from missing timing', async () => {
  const { formatDuration } = await import('../../web/utils/time');
  expect(formatDuration(0)).toBe('0ms');
  expect(formatDuration(null)).toBe('—');
  expect(formatDuration(120000)).toBe('2m');
});


it('retains scalar values in historical field arrays', () => {
  expect(parseApprovalCardContent(JSON.stringify([{label:'数量',value:3},{label:'通过',value:true}])).fields)
    .toEqual([{label:'数量',value:'3'},{label:'通过',value:'true'}]);
});
