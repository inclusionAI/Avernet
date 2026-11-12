import express, { type Express } from 'express';
import { once } from 'node:events';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createWorkflowCloneRouter } from '../workflow-clone.js';
import { WorkflowCloneError, type WorkflowCloneService } from '../../services/workflow-clone-service.js';

function mockService(): WorkflowCloneService {
  return {
    candidates: vi.fn(),
    preview: vi.fn(),
    clone: vi.fn(),
  } as unknown as WorkflowCloneService;
}

let app: Express;
let server: ReturnType<Express['listen']> | null = null;
let baseUrl = '';

async function listen(): Promise<string> {
  server = app.listen(0, '127.0.0.1');
  await once(server, 'listening');
  const address = server.address();
  if (!address || typeof address === 'string') throw new Error('test server did not bind');
  baseUrl = `http://127.0.0.1:${address.port}`;
  return baseUrl;
}

beforeEach(() => {
  app = express();
  app.use(express.json());
});

afterEach(async () => {
  const active = server;
  server = null;
  if (active) await new Promise<void>(resolve => active.close(() => resolve()));
  vi.restoreAllMocks();
});

describe('POST and GET /api/workflows/clone*', () => {
  it('requires an identity for every endpoint', async () => {
    const service = mockService();
    app.use('/api/workflows', createWorkflowCloneRouter(service));
    const base = await listen();

    const candidate = await fetch(`${base}/api/workflows/clone-candidates`);
    const preview = await fetch(`${base}/api/workflows/clone/preview`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
    const clone = await fetch(`${base}/api/workflows/clone`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });

    expect(candidate.status).toBe(401);
    expect(preview.status).toBe(401);
    expect(clone.status).toBe(401);
    expect(await candidate.json()).toMatchObject({ code: 'IDENTITY_REQUIRED' });
  });

  it('returns all service candidates without applying a permission filter', async () => {
    const service = mockService();
    vi.mocked(service.candidates).mockResolvedValue([
      { workflowId: 'shared', title: 'Shared', packId: 'pack', updatedAt: 1 },
    ]);
    app.use('/api/workflows', createWorkflowCloneRouter(service));
    const base = await listen();

    const response = await fetch(`${base}/api/workflows/clone-candidates`, {
      headers: { Cookie: 'staff_id=user-1' },
    });

    expect(response.status).toBe(200);
    expect(response.headers.get('cache-control')).toBe('no-store');
    expect(await response.json()).toEqual({ data: [{ workflowId: 'shared', title: 'Shared', packId: 'pack', updatedAt: 1 }] });
  });

  it('maps service validation failures to HTTP status codes', async () => {
    const service = mockService();
    vi.mocked(service.preview).mockRejectedValue(new WorkflowCloneError(409, 'SOURCE_PACK_INVALID', 'invalid pack'));
    app.use('/api/workflows', createWorkflowCloneRouter(service));
    const base = await listen();

    const response = await fetch(`${base}/api/workflows/clone/preview`, {
      method: 'POST', headers: { Cookie: 'staff_id=user-1', 'Content-Type': 'application/json' },
      body: JSON.stringify({ sourceWorkflowId: 'shared' }),
    });

    expect(response.status).toBe(409);
    expect(response.headers.get('cache-control')).toBe('no-store');
    expect(await response.json()).toMatchObject({ code: 'SOURCE_PACK_INVALID', message: 'invalid pack' });
  });

  it('does not leak unexpected internal error messages', async () => {
    const service = mockService();
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    vi.mocked(service.preview).mockRejectedValue(new Error('secret database path and credentials'));
    app.use('/api/workflows', createWorkflowCloneRouter(service));
    const base = await listen();

    const response = await fetch(`${base}/api/workflows/clone/preview`, {
      method: 'POST', headers: { Cookie: 'staff_id=user-1', 'Content-Type': 'application/json' },
      body: JSON.stringify({ sourceWorkflowId: 'shared' }),
    });

    expect(response.status).toBe(500);
    const body = await response.json();
    expect(body).toEqual({ error: 'Internal Server Error', code: 'INTERNAL_ERROR' });
    expect(JSON.stringify(body)).not.toContain('secret database path and credentials');
    expect(consoleError).toHaveBeenCalledTimes(1);
  });

  it('creates a clone and invalidates the workflow cache', async () => {
    const service = mockService();
    const result = {
      sourceWorkflowId: 'shared', sourcePackId: 'pack', sourceTitle: 'Shared', baseCommit: 'a', scriptCount: 1,
      targetWorkflowId: 'mine', targetPackId: 'mine', branch: 'wf/mine', commit: 'b',
    };
    vi.mocked(service.clone).mockResolvedValue(result);
    app.use('/api/workflows', createWorkflowCloneRouter(service));
    const base = await listen();

    const response = await fetch(`${base}/api/workflows/clone`, {
      method: 'POST', headers: { Cookie: 'staff_id=user-1', 'Content-Type': 'application/json' },
      body: JSON.stringify({ sourceWorkflowId: 'shared', targetWorkflowId: 'mine', targetTitle: 'Mine', facade: {} }),
    });

    expect(response.status).toBe(201);
    expect(await response.json()).toEqual(result);
    expect(service.clone).toHaveBeenCalledWith(
      { sourceWorkflowId: 'shared', targetWorkflowId: 'mine', targetTitle: 'Mine', facade: {} },
      'user-1',
    );
  });
});
