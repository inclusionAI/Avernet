import { afterEach, describe, expect, it, vi } from 'vitest';
const f = vi.hoisted(() => ({ tick: vi.fn(), close: vi.fn(), security: vi.fn(), router: vi.fn(), construct: vi.fn() }));
vi.mock('@avernet/workflow/server/services/callback-compensation/service', () => ({ CallbackCompensationService: class {
  constructor(...args: unknown[]) { f.construct(...args); }
  tick = f.tick; close = f.close;
} }));
vi.mock('@avernet/workflow/server/routes/callback-compensations', () => ({
  callbackCompensationSecurity: () => f.security, createCallbackCompensationsRouter: () => f.router,
}));
import { createCallbackCompensationRuntime } from '../callback-compensation-runtime.js';
import { fetchTransport } from '@avernet/workflow/server/services/callback-compensation/transport';
afterEach(() => { vi.useRealTimers(); vi.resetAllMocks(); });
describe('callback compensation worker lifecycle', () => {
  it('does not poll or deliver without both reviewed evidence and key', async () => {
    vi.useFakeTimers(); const runtime = createCallbackCompensationRuntime({} as never, 'pre');
    expect(f.construct).toHaveBeenCalledWith({}, expect.objectContaining({ environment: 'pre', transport: fetchTransport }));
    runtime.start(); await vi.advanceTimersByTimeAsync(30000);
    expect(f.tick).not.toHaveBeenCalled(); await runtime.stop();
  });
  it('does not overlap ticks and stops claiming queued work before draining an in-flight send', async () => {
    vi.useFakeTimers(); let release!: () => void;
    const inFlight = new Promise<void>(resolve => { release = resolve; }); f.tick.mockReturnValue(inFlight);
    f.close.mockImplementation(() => { release(); return inFlight; });
    const runtime = createCallbackCompensationRuntime({} as never, 'pre', {
      principal: async () => ({ actorId: 'admin', isAdmin: true }), trustedOrigins: [],
      evidence: { resolve: async () => null }, protectionKey: Buffer.alloc(32),
    });
    runtime.start(); runtime.start(); await vi.advanceTimersByTimeAsync(20000);
    expect(f.tick).toHaveBeenCalledTimes(1);
    await runtime.stop(); expect(f.close).toHaveBeenCalledOnce();
    await vi.advanceTimersByTimeAsync(30000); expect(f.tick).toHaveBeenCalledTimes(1);
  });
  it('exposes separate early security and authenticated route mounts', () => {
    const runtime = createCallbackCompensationRuntime({} as never, 'pre'); const use = vi.fn();
    runtime.mountSecurity({ use } as never); runtime.mountRoutes({ use } as never);
    expect(use.mock.calls).toEqual([['/api/workflows', f.security], ['/api/workflows', f.router]]);
  });
});
