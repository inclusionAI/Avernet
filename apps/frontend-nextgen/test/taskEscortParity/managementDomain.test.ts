import { managementJobStatus, managementSourceLabel, managementTime } from '@/domain/taskEscortParity/management';
import type { ManagementJob, ManagementVersionSource } from '@/domain/taskEscortParity/managementTypes';
import { managementController } from '@/services/backendApi/harnessFlow/managementController';
import { backendRequest } from '@/services/backendApi/httpClient';
import { managementService } from '@/services/taskEscortParity/managementService';
import { maskedUrl, safeSpecJson } from '@/services/taskEscortParity/managementValidation';
import { readFileSync } from 'node:fs';

jest.mock('@/services/backendApi/httpClient', () => ({ backendRequest: jest.fn() }));
const request = jest.mocked(backendRequest);
const signal = new AbortController().signal;
const job: ManagementJob = {
  id: 'job',
  kind: 'auto_run',
  status: 'done',
  attempts: 0,
  reason: null,
  taskId: null,
  createdAtMs: null,
  flowId: null,
  analysisId: null,
  complete: null,
};
beforeEach(() => request.mockReset());
it('does not misrepresent incomplete/unknown evidence or missing timestamps', () => {
  expect(managementTime(null)).toBe('时间未记录');
  expect(managementTime(1794873600000)).toContain('2026');
  expect(managementJobStatus(job)).toContain('待核对');
  expect(managementJobStatus({ ...job, complete: true })).toBe('分析结束');
  expect(managementJobStatus({ ...job, complete: false })).toContain('不完整');
  expect(managementJobStatus({ ...job, kind: 'auto_notice', status: 'blocked' })).toBe('需要处理');
  expect(managementJobStatus({ ...job, kind: 'future_kind', status: 'future_status' })).toBe('future_status');
  expect(managementSourceLabel(null)).toBe('来源未知');
  const source: ManagementVersionSource = {
    kind: 'manual_optimization',
    deployNumber: 1,
    baselineDeployNumber: null,
    taskId: null,
    revision: null,
    automationRoundId: null,
    actorId: null,
    effectiveAtMs: null,
    restoredFromDeployNumber: null,
  };
  expect(managementSourceLabel(source)).toBe('手动优化 · 人工确认发布');
  expect(managementSourceLabel({ ...source, kind: 'rollback' })).toBe('回滚');
  expect(managementSourceLabel({ ...source, kind: 'rollback', restoredFromDeployNumber: 7 })).toBe('回滚至 #7');
});
it('redacts URL credentials, sensitive queries and fragments without contacting the callback', () => {
  expect(maskedUrl('not a url')).toBe('[地址格式无效]');
  expect(maskedUrl('javascript:alert(1)')).toBe('[非 HTTP 地址已隐藏]');
  const callback = new URL('https://example.test/');
  callback.username = 'user';
  callback.password = 'dummy';
  callback.searchParams.set('api_key', 'test-token');
  callback.searchParams.set('plain', 'ok');
  callback.hash = 'fragment';
  expect(maskedUrl(callback.toString())).not.toMatch(/dummy|test-token|fragment/);
  expect(request).not.toHaveBeenCalled();
});
it('maps nullable policy fields to unknown rather than inventing environment/impact/recipients', async () => {
  request.mockResolvedValue({
    workflowId: 'wf',
    enabled: true,
    autoDraft: false,
    dailyTime: '00:00',
    timezone: 'Asia/Shanghai',
    version: 0,
    updatedAtMs: 0,
    dailyConfigured: false,
    source: 'default',
  });
  expect(await managementService.policy('wf', 'actor', signal)).toMatchObject({
    executionEnvironment: null,
    minimumImpact: null,
    notificationRecipients: null,
    notificationVersion: null,
    dailyConfigured: false,
  });
});
it('reads the real deploy snapshot and nullable old-journal status fields', async () => {
  request.mockResolvedValue({
    workflowId: 'wf',
    version: 1,
    deployNumber: 3,
    action: 'deploy',
    gmtCreate: 1794873600,
    specJson: '{}',
  });
  expect(await managementService.deploy('wf', 'actor', 3, signal)).toMatchObject({ version: 1, deployNumber: 3 });
  request.mockResolvedValue({
    schemaVersion: 'workflow-automation/v1',
    workflowId: 'wf',
    lastScannedAtMs: null,
    pauseReason: null,
    coverage: { total: 0, pending: 0, failed: 0, partial: 0, unknown: 0 },
    jobs: [],
  });
  expect(await managementService.status('wf', 'actor', signal)).toMatchObject({
    retryableJobs: null,
    round: null,
    lastScannedAtMs: null,
  });
});
it('owns a GET-only transport with no Domain imports, ambient actor or external network path', () => {
  const source = readFileSync(require.resolve('@/services/backendApi/harnessFlow/managementController'), 'utf8');
  expect(source).not.toMatch(/from\s+['"]@\/domain\//);
  expect(source.match(/method:\s*['"]GET['"]/g)).toHaveLength(1);
  expect(source).not.toMatch(/['"](?:POST|PUT|PATCH|DELETE)['"]|https?:\/\/|\bfetch\(/);
  expect(source).toContain('injectUserId: false');
  expect(Object.keys(managementController)).not.toContain('save');
});

it('redacts credentials embedded in legacy YAML content before snapshot enters React state', () => {
  const json = JSON.stringify({ content: 'id: wf\nnodes:\n  - executor:\n      iamToken: DUMMY_SECRET_SENTINEL' });
  expect(safeSpecJson(json)).not.toContain('DUMMY_SECRET_SENTINEL');
});
