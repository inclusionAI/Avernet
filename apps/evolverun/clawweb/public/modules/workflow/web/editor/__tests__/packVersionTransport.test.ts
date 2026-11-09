import { afterEach, expect, it, vi } from 'vitest'
import { putPackVersion } from '../packVersionSession'

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks() })
function readBlob(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result))
    reader.onerror = reject
    reader.readAsText(blob)
  })
}
it('sends an uncompressed JSON Blob with the CSRF header through the real fetchJson, including above its gzip threshold', async () => {
  const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ branch: 'wf/workflow', commit: 'a'.repeat(40) }) })
  const compressor = vi.fn(() => { throw new Error('unexpected compression') })
  vi.stubGlobal('fetch', fetchMock); vi.stubGlobal('CompressionStream', compressor)
  const input = { sourceVersion: 'draft' as const, sourceCommit: 'b'.repeat(40), expectedSavedHead: 'b'.repeat(40), changes: [
    { operation: 'replace' as const, path: 'scripts/名称', contentBase64: 'YWJj'.repeat(7000), mode: '100755' as const },
  ] }
  await putPackVersion('workflow', input)
  expect(compressor).not.toHaveBeenCalled()
  expect(fetchMock).toHaveBeenCalledTimes(1)
  const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
  expect(url).toBe('/api/workflows/workflow/pack-version')
  expect(init.method).toBe('PUT')
  expect(init.headers).toMatchObject({ 'Content-Type': 'application/json', 'X-Pack-Version-CSRF': '1' })
  expect(init.headers).not.toHaveProperty('Content-Encoding')
  expect(init.body).toBeInstanceOf(Blob)
  expect(JSON.parse(await readBlob(init.body as Blob))).toEqual(input)
  expect((init.body as Blob).size).toBeGreaterThan(14 * 1024)
})

it('is accepted by a loopback JSON parser with inflate disabled and an explicit CSRF-header guard', async () => {
  const { default: express } = await import('express')
  const http = await import('node:http')
  const app = express()
  let received: unknown
  app.use(express.json({ limit: '1mb', inflate: false }))
  app.put('/api/workflows/workflow/pack-version', (req, res) => {
    if (req.get('X-Pack-Version-CSRF') !== '1' || req.get('Content-Encoding')) { res.status(403).end(); return }
    received = req.body
    res.json({ branch: 'wf/workflow', commit: 'a'.repeat(40) })
  })
  const server = await new Promise<import('node:http').Server>(resolve => { const s = app.listen(0, '127.0.0.1', () => resolve(s)) })
  try {
    const address = server.address()
    if (!address || typeof address === 'string') throw new Error('No test port')
    vi.stubGlobal('fetch', async (url: string, init: RequestInit) => {
      const body = await readBlob(init.body as Blob)
      return new Promise((resolve, reject) => {
        const request = http.request({ host: '127.0.0.1', port: address.port, path: url, method: init.method, headers: init.headers as Record<string, string> }, response => {
          let data = ''
          response.setEncoding('utf8'); response.on('data', chunk => { data += chunk }); response.on('end', () => resolve({ ok: response.statusCode === 200, status: response.statusCode, json: async () => JSON.parse(data), text: async () => data }))
        })
        request.on('error', reject); request.end(body)
      })
    })
    const input = { sourceVersion: 'draft' as const, sourceCommit: 'b'.repeat(40), expectedSavedHead: 'b'.repeat(40), changes: [
      { operation: 'create' as const, path: 'scripts/large', contentBase64: 'YWJj'.repeat(7000), mode: '100644' as const },
    ] }
    const result = await putPackVersion('workflow', input)
    expect(result.branch).toBe('wf/workflow')
    expect(received).toEqual(input)
  } finally {
    await new Promise<void>((resolve, reject) => server.close(error => error ? reject(error) : resolve()))
  }
})
