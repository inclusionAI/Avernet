import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { DiagnosisRecord, getDiagnosisTitle } from '../DiagnosisRecord';
import type { DiagnosisItem } from '../../../../types/monitoring';

vi.mock('../session-recovery/RecoveryTaskLinks', () => ({
  RecoveryTaskLinks: ({ eventId }: { eventId: string }) => <span data-testid="recovery-links">{eventId}</span>,
}));
const base: DiagnosisItem = {
  diagnosisId: 'diag-1', botId: 'bot-1', decision: 'PASS', occurredAt: '2026-10-26T00:00:00Z', diagnosedAt: '2026-10-26T00:01:00Z',
  sessionKey: null, sessionId: 'session-1', traceId: 'trace-1', tcFaultLabel: 'TC.MCP.DATA', confidence: .86,
  businessProblemCategory: null, businessProblemSubtype: null, systemDiagnosis: '系统诊断原文', businessDiagnosis: '业务诊断原文',
  handlerName: 'COMPLETED', humanIntervention: true,
};
afterEach(cleanup);

describe('diagnosis record title', () => {
  it.each<[Partial<DiagnosisItem>, string]>([
    [{}, '任务已完成'],
    [{ handlerName: 'NOT_COMPLETED' }, '任务尚未完成'],
    [{ handlerName: null }, '任务完成状态未知'],
    [{ handlerName: '' }, '任务完成状态未知'],
    [{ handlerName: 'historical-handler' }, '任务完成状态未知'],
    [{ businessProblemCategory: '', businessProblemSubtype: ' \t ' }, '任务已完成'],
    [{ businessProblemCategory: ' 外部服务异常 ', businessProblemSubtype: ' 数据获取失败 ' }, '外部服务异常 · 数据获取失败'],
    [{ businessProblemCategory: '外部服务异常' }, '外部服务异常'],
    [{ businessProblemSubtype: '数据获取失败' }, '数据获取失败'],
    [{ businessProblemCategory: ' ', businessProblemSubtype: ' 数据获取失败 ' }, '数据获取失败'],
    [{ decision: 'ALERT' }, '异常告警'],
    [{ decision: 'ALERT', handlerName: 'NOT_COMPLETED', businessProblemCategory: ' ', businessProblemSubtype: '' }, '异常告警'],
    [{ decision: 'ALERT', businessProblemCategory: '外部服务异常', businessProblemSubtype: '数据获取失败' }, '外部服务异常 · 数据获取失败'],
    [{ decision: 'UNRESOLVED' }, '诊断失败'],
    [{ decision: 'UNRESOLVED', businessProblemCategory: '旧分类', businessProblemSubtype: '旧子类' }, '诊断失败'],
  ])('maps %j to %s without mutating data', (patch, expected) => {
    const item = Object.freeze({ ...base, ...patch });
    expect(getDiagnosisTitle(item)).toBe(expected);
    render(<DiagnosisRecord item={item} open={false} toggle={vi.fn()} />);
    expect(screen.getByText(expected)).toHaveAttribute('title', expected);
    expect(screen.getByRole('button')).toHaveTextContent('trace-1');
  });
  it('preserves raw classification and uncertain field labels when diagnosis fails', () => {
    render(<DiagnosisRecord item={{ ...base, decision: 'UNRESOLVED', businessProblemCategory: '旧分类', businessProblemSubtype: '旧子类' }} open toggle={vi.fn()} />);
    expect(screen.getByText('诊断失败')).toHaveAttribute('title', '诊断失败');
    expect(screen.getByText('任务是否完成').nextElementSibling).toHaveTextContent('未判定');
    expect(screen.getByText('是否人工纠错').nextElementSibling).toHaveTextContent('未判定');
    expect(screen.getByText('业务问题类型').nextElementSibling).toHaveTextContent('旧分类');
    expect(screen.getByText('业务问题子类型').nextElementSibling).toHaveTextContent('旧子类');
  });
});

describe.each(['claw-monitoring/diagnosis-event/v4', 'claw-monitoring/diagnosis-event/v3', 'claw-monitoring/diagnosis-event/v2', undefined])('no redundant policy row for %s', schemaVersion => {
  it.each(['PASS', 'ALERT', 'UNRESOLVED'] as const)('retains details and recovery links for %s', decision => {
    const { container } = render(<DiagnosisRecord item={{ ...base, schemaVersion, decision }} open toggle={vi.fn()} />);
    expect(screen.queryByText(/告警判定：|统一口径 v1|旧口径|版本未知/)).not.toBeInTheDocument();
    expect(container.querySelector('[title*="PASS 不等于"]')).toBeNull();
    expect(screen.getByText('系统诊断原文')).toBeInTheDocument();
    expect(screen.getByText('业务诊断原文')).toBeInTheDocument();
    expect(screen.getByText('TC 故障标签').nextElementSibling).toHaveTextContent('TC.MCP.DATA');
    expect(screen.getByText('诊断置信度').nextElementSibling).toHaveTextContent('86.0%');
    expect(screen.getByTestId('recovery-links')).toHaveTextContent('diag-1');
    expect(screen.getByRole('button', { name: '复制Trace ID' })).toBeInTheDocument();
  });
});
