import { backendRequest } from '@/services/backendApi/httpClient';
import {
  cancelRun,
  getRun,
  getRunDetail,
  listRuns,
  requiresIdentitySource,
  submitRun,
  tenantParams,
  validateIdentitySource,
} from '@/services/agentswarm/service';

jest.mock('@/services/backendApi/httpClient', () => ({
  ...jest.requireActual('@/services/backendApi/httpClient'),
  backendRequest: jest.fn(),
}));

const request = backendRequest as jest.Mock;
const ok = (data: unknown) => ({ code: 200000, data });

beforeEach(() => request.mockReset());

const botAgent = { name: 'worker', role: 'does work', kind: 'bot' as const };
const llmAgent = { name: 'planner', role: 'plans', kind: 'llm' as const };

test('every call carries the tenant parameter', async () => {
  request.mockResolvedValue(ok({ run_id: 'r1', status: 'RUNNING', items: [], total: 0, page: 1, page_size: 20, agents: [] }));
  await submitRun('acme', { tenant: 'acme', goal: 'g', agents: [llmAgent] });
  await getRun('acme', 'r1');
  await listRuns('acme', { page: 2 });
  await getRunDetail('acme', 'r1');
  await cancelRun('acme', 'r1');
  for (const [, options] of request.mock.calls) {
    expect(options.params).toMatchObject({ tenant: 'acme' });
  }
});

test('exact URLs for each operation', async () => {
  request.mockResolvedValue(ok({ run_id: 'r1', status: 'RUNNING', items: [], total: 0, page: 1, page_size: 20, agents: [] }));
  await getRun('acme', 'r1');
  await getRunDetail('acme', 'r1');
  await cancelRun('acme', 'r1');
  await listRuns('acme');
  expect(request.mock.calls.map(([url]) => url)).toEqual([
    '/openapi/v1/agentswarm/runs/r1',
    '/openapi/v1/agentswarm/runs/r1/detail',
    '/openapi/v1/agentswarm/runs/r1/cancel',
    '/openapi/v1/agentswarm/runs',
  ]);
  expect(request.mock.calls[2][1].method).toBe('POST');
});

test('a run id is encoded so an id cannot escape its path segment', async () => {
  request.mockResolvedValue(ok({ run_id: 'r1' }));
  await getRun('acme', 'a/../b');
  expect(request.mock.calls[0][0]).toBe('/openapi/v1/agentswarm/runs/a%2F..%2Fb');
});

test('an empty tenant is refused before any request is made', () => {
  expect(() => tenantParams('   ')).toThrow('租户');
});

test('identity_bot_id is sent for a bot-kind run', async () => {
  request.mockResolvedValue(ok({ run_id: 'r1' }));
  await submitRun('acme', { tenant: 'acme', goal: 'g', agents: [botAgent], identity_bot_id: ' bot-1 ' });
  expect(request.mock.calls[0][1].data).toMatchObject({ identity_bot_id: 'bot-1' });
});

test('identity_bot_id is omitted entirely for an llm-only run', async () => {
  request.mockResolvedValue(ok({ run_id: 'r1' }));
  await submitRun('acme', { tenant: 'acme', goal: 'g', agents: [llmAgent] });
  expect(request.mock.calls[0][1].data.identity_bot_id).toBeUndefined();
});

test('an llm-only run is exempt from the identity requirement', () => {
  expect(requiresIdentitySource([llmAgent])).toBe(false);
  expect(validateIdentitySource({ agents: [llmAgent] })).toBeNull();
});

test('a bot-kind run without a source is refused locally, not sent to the gateway', async () => {
  expect(validateIdentitySource({ agents: [botAgent] })).toContain('身份 Bot');
  await expect(submitRun('acme', { tenant: 'acme', goal: 'g', agents: [botAgent] })).rejects.toThrow('身份 Bot');
  expect(request).not.toHaveBeenCalled();
});

test('a blank source counts as absent', () => {
  expect(validateIdentitySource({ agents: [botAgent], identity_bot_id: '   ' })).toContain('身份 Bot');
});

test('one bot-kind agent among llm agents still requires a source', () => {
  expect(validateIdentitySource({ agents: [llmAgent, botAgent] })).toContain('身份 Bot');
});