/** @jest-environment jsdom */
import { act, renderHook } from '@testing-library/react';
import { useDwsEligibility } from '../../src/hooks/useDwsEligibility';
import { dwsHostingController } from '../../src/services/backendApi/bots/dwsHostingController';

jest.mock('../../src/services/backendApi/bots/dwsHostingController', () => ({
  dwsHostingController: { eligibility: jest.fn(), status: jest.fn() },
}));
const eligibility = jest.mocked(dwsHostingController.eligibility);
const status = jest.mocked(dwsHostingController.status);
const supported = {
  enabled: false,
  running: false,
  intake_ready: false,
  error: '',
  authorization: 'required',
  challenge: null,
  unknown_executions: 0,
  unknown_deliveries: 0,
};
beforeEach(() => {
  jest.useFakeTimers();
  eligibility.mockReset();
  status.mockReset().mockResolvedValue({ code: 200000, data: supported } as any);
});
afterEach(() => jest.useRealTimers());
const settle = async () => {
  await act(async () => {
    await Promise.resolve();
  });
};

test('unrelated engines and missing identities never request eligibility', () => {
  const { result, rerender } = renderHook(({ actor, eligible }) => useDwsEligibility('b', actor, eligible), {
    initialProps: { actor: 'u', eligible: false },
  });
  expect(result.current).toBe(false);
  rerender({ actor: '', eligible: true });
  expect(result.current).toBe(false);
  expect(eligibility).not.toHaveBeenCalled();
  expect(status).not.toHaveBeenCalled();
});

test('hidden until positive grant, then hidden again on revocation or failure', async () => {
  eligibility.mockResolvedValue({ code: 200000, data: { available: true } } as any);
  const { result } = renderHook(() => useDwsEligibility('b', 'u', true));
  expect(result.current).toBe(false);
  await settle();
  expect(result.current).toBe(true);
  eligibility.mockResolvedValue({ code: 200000, data: { available: false } } as any);
  await act(async () => {
    jest.advanceTimersByTime(30000);
  });
  expect(result.current).toBe(false);
  eligibility.mockRejectedValue(new Error('unavailable'));
  await act(async () => {
    jest.advanceTimersByTime(30000);
  });
  expect(result.current).toBe(false);
});

test('identity changes do not reuse the previous grant and unmount aborts', async () => {
  eligibility.mockResolvedValueOnce({ code: 200000, data: { available: true } } as any);
  const { result, rerender, unmount } = renderHook(({ actor }) => useDwsEligibility('b', actor, true), {
    initialProps: { actor: 'u' },
  });
  await settle();
  expect(result.current).toBe(true);
  eligibility.mockImplementation(() => new Promise(() => {}));
  rerender({ actor: 'other' });
  expect(result.current).toBe(false);
  const signal = eligibility.mock.calls.at(-1)![1]!;
  unmount();
  expect(signal.aborted).toBe(true);
});

test('failed envelope never grants even when data says available', async () => {
  eligibility.mockResolvedValue({ code: 403000, data: { available: true } } as any);
  const { result } = renderHook(() => useDwsEligibility('b', 'u', true));
  await settle();
  expect(result.current).toBe(false);
  expect(status).not.toHaveBeenCalled();
});

test.each([null, {}, { enabled: true }, { ...supported, running: 'false' }])(
  'malformed status hides the entry: %j',
  async (data) => {
    eligibility.mockResolvedValue({ code: 200000, data: { available: true } } as any);
    status.mockResolvedValue({ code: 200000, data } as any);
    const { result } = renderHook(() => useDwsEligibility('b', 'u', true));
    await settle();
    expect(result.current).toBe(false);
  },
);

test('unavailable engine hides entry and polling restores it without authorization', async () => {
  eligibility.mockResolvedValue({ code: 200000, data: { available: true } } as any);
  status.mockResolvedValueOnce({ code: 502000, data: supported } as any);
  const { result } = renderHook(() => useDwsEligibility('b', 'u', true));
  await settle();
  expect(result.current).toBe(false);
  await act(async () => {
    jest.advanceTimersByTime(30000);
  });
  expect(result.current).toBe(true);
});

test('late status for a previous bot cannot expose the new bot entry', async () => {
  eligibility.mockResolvedValue({ code: 200000, data: { available: true } } as any);
  let finish!: (value: any) => void;
  status.mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  const { result, rerender } = renderHook(({ bot }) => useDwsEligibility(bot, 'u', true), {
    initialProps: { bot: 'a' },
  });
  await settle();
  status.mockRejectedValue(new Error('unsupported'));
  rerender({ bot: 'b' });
  await act(async () => {
    finish({ code: 200000, data: supported });
  });
  expect(result.current).toBe(false);
  expect(status.mock.calls[0][1]?.aborted).toBe(true);
});

test('timed-out status cannot expose entry when it resolves late', async () => {
  eligibility.mockResolvedValue({ code: 200000, data: { available: true } } as any);
  let finish!: (value: any) => void;
  status.mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  const { result } = renderHook(() => useDwsEligibility('b', 'u', true));
  await settle();
  await act(async () => {
    jest.advanceTimersByTime(5000);
  });
  await act(async () => {
    finish({ code: 200000, data: supported });
  });
  expect(result.current).toBe(false);
});
