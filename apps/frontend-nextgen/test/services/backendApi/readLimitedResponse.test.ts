import { readLimitedResponse, ResponseTooLargeError } from '@/services/backendApi/readLimitedResponse';

it('reads UTF-8 across transport chunks and closes the reader', async () => {
  const bytes = new TextEncoder().encode('知识库');
  const stream = new ReadableStream({
    start(controller) {
      controller.enqueue(bytes.slice(0, 2));
      controller.enqueue(bytes.slice(2));
      controller.close();
    },
  });
  await expect(readLimitedResponse(new Response(stream), 64)).resolves.toBe('知识库');
  expect(stream.locked).toBe(false);
});
it('cancels an oversized chunked response before consuming the whole object', async () => {
  let pulls = 0;
  const cancel = jest.fn();
  const stream = new ReadableStream(
    {
      pull(controller) {
        pulls++;
        controller.enqueue(new Uint8Array(1024));
      },
      cancel,
    },
    { highWaterMark: 0 },
  );
  await expect(readLimitedResponse(new Response(stream), 2048)).rejects.toThrow(ResponseTooLargeError);
  expect(pulls).toBe(3);
  expect(cancel).toHaveBeenCalledTimes(1);
  expect(stream.locked).toBe(false);
});
it('rejects a declared oversized response without reading', async () => {
  const cancel = jest.fn();
  const stream = new ReadableStream({ cancel }, { highWaterMark: 0 });
  await expect(
    readLimitedResponse(new Response(stream, { headers: { 'content-length': '5000' } }), 1024),
  ).rejects.toThrow(ResponseTooLargeError);
  expect(cancel).toHaveBeenCalledTimes(1);
});
it('reports a human-readable limit in the size error', async () => {
  const readError = async (contentLength: string, limit: number) => {
    const stream = new ReadableStream({ cancel: jest.fn() }, { highWaterMark: 0 });
    return readLimitedResponse(new Response(stream, { headers: { 'content-length': contentLength } }), limit).then(
      () => undefined,
      (error: unknown) => error,
    );
  };
  const kibError = await readError('5000', 1024);
  expect(kibError).toBeInstanceOf(ResponseTooLargeError);
  expect((kibError as Error).message).toContain('1 KiB');
  const mibError = await readError(String(3 * 1024 * 1024), 2 * 1024 * 1024);
  expect(mibError).toBeInstanceOf(ResponseTooLargeError);
  expect((mibError as Error).message).toContain('2.0 MiB');
});
it('propagates network failures and releases the stream', async () => {
  const stream = new ReadableStream({
    start(c) {
      c.error(new Error('lost connection'));
    },
  });
  await expect(readLimitedResponse(new Response(stream), 2048)).rejects.toThrow('lost connection');
  expect(stream.locked).toBe(false);
});
