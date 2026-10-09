import { isTransientError, retryOnTransient } from '../../src/utils/retryRequest';

describe('retryRequest', () => {
  test('识别瞬时错误和非瞬时错误', () => {
    expect(isTransientError({ response: { status: 502 } })).toBe(true);
    expect(isTransientError(new Error('Network Error'))).toBe(true);
    expect(isTransientError({ response: { status: 403 } })).toBe(false);
  });

  test('瞬时错误重试后返回成功结果', async () => {
    let count = 0;
    const result = await retryOnTransient(
      async () => {
        count += 1;
        if (count < 2) throw { response: { status: 503 } };
        return 'ok';
      },
      { baseDelayMs: 1, retries: 2 },
    );

    expect(result).toBe('ok');
    expect(count).toBe(2);
  });

  test('非瞬时错误不重试', async () => {
    let count = 0;
    await expect(
      retryOnTransient(
        async () => {
          count += 1;
          throw { response: { status: 400 } };
        },
        { baseDelayMs: 1, retries: 2 },
      ),
    ).rejects.toEqual({ response: { status: 400 } });
    expect(count).toBe(1);
  });
});
