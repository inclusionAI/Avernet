import { afterEach, describe, expect, it, vi } from 'vitest'
import { repairBatches } from './repair-batches'
import { gunzipSync } from 'node:zlib'

afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals() })

describe('bounded repair reads', () => {
  it('shares identical in-flight candidate reads without retaining stale results or sharing mutable responses', async () => {
    vi.stubGlobal('__TERN__', { user: { outUserNo: 'reader' } })
    const finishes: Array<() => void> = []
    const urls: string[] = []
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      urls.push(url)
      await new Promise<void>(resolve => { finishes.push(resolve) })
      return { ok: true, json: async () => ({ items: [{ itemId: 'original' }] }) }
    }))
    const first = repairBatches.candidates('wf-1', { metadataPage: 1, page: 1 })
    const second = repairBatches.candidates('wf-1', { page: 1, metadataPage: 1 })
    finishes.forEach(finish => finish())
    const [a, b] = await Promise.all([first, second])
    expect(urls).toHaveLength(1)
    a.items[0].itemId = 'consumer-edit'
    expect(b.items[0].itemId).toBe('original')
    const next = repairBatches.candidates('wf-1', { metadataPage: 1, page: 1 })
    expect(urls).toHaveLength(2)
    finishes.at(-1)!(); await next
  })
  it('does not combine different principals or candidate pages', async () => {
    const finishes: Array<() => void> = [], observed: string[] = []
    vi.stubGlobal('fetch', vi.fn(async (url: string, init: RequestInit) => {
      const identity = `${new Headers(init.headers).get('X-User-Id')}:${new URL(url, 'http://localhost').searchParams.get('page')}`
      observed.push(identity)
      await new Promise<void>(resolve => finishes.push(resolve))
      return { ok: true, json: async () => ({ items: [{ itemId: identity }] }) }
    }))
    vi.stubGlobal('__TERN__', { user: { outUserNo: 'reader-a' } })
    const a = repairBatches.candidates('wf-1', { metadataPage: 1, page: 1 })
    vi.stubGlobal('__TERN__', { user: { outUserNo: 'reader-b' } })
    const b = repairBatches.candidates('wf-1', { metadataPage: 1, page: 1 })
    const page = repairBatches.candidates('wf-1', { metadataPage: 1, page: 2 })
    finishes.forEach(finish => finish())
    expect((await Promise.all([a, b, page])).map(result => result.items[0].itemId)).toEqual(['reader-a:1', 'reader-b:1', 'reader-b:2'])
    expect(observed).toEqual(['reader-a:1', 'reader-b:1', 'reader-b:2'])
  })
  it('evicts failed candidate reads so an explicit retry makes a fresh request', async () => {
    vi.stubGlobal('__TERN__', { user: { outUserNo: 'reader' } })
    const urls: string[] = []
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      urls.push(url)
      if (urls.length === 1) throw new Error('controlled read failure')
      return { ok: true, json: async () => ({ items: [] }) }
    }))
    await expect(repairBatches.candidates('wf-1', { metadataPage: 1 })).rejects.toThrow('controlled read failure')
    expect((await repairBatches.candidates('wf-1', { metadataPage: 1 })).items).toEqual([])
    expect(urls).toHaveLength(2)
  })
  it('does not share a response before the browser principal is known', async () => {
    const finishes: Array<() => void> = []
    let calls = 0
    vi.stubGlobal('fetch', vi.fn(async () => {
      calls++
      await new Promise<void>(resolve => finishes.push(resolve))
      return { ok: true, json: async () => ({ items: [] }) }
    }))
    const a = repairBatches.candidates('wf-1', { metadataPage: 1 })
    const b = repairBatches.candidates('wf-1', { metadataPage: 1 })
    finishes.forEach(finish => finish())
    await Promise.all([a, b])
    expect(calls).toBe(2)
  })

  it('sends a 50-issue preview scope in the body without an oversized request URL', async () => {
    const previewSignatures = Array.from({ length: 50 }, (_, i) => `${i}:${'节点'.repeat(250)}`)
    let requestUrl = '', requestInit: RequestInit | undefined
    vi.stubGlobal('fetch', vi.fn(async (url: string, init: RequestInit) => {
      requestUrl = url; requestInit = init
      return { ok: true, json: async () => ({ issuePreviews: [] }) }
    }))
    await repairBatches.candidates('wf-1', { pageSize: 1, previewSignatures })
    expect(new TextEncoder().encode(requestUrl).length).toBeLessThan(1024)
    expect(requestInit?.method).toBe('POST')
    const body = new Headers(requestInit?.headers).get('Content-Encoding') === 'gzip'
      ? gunzipSync(requestInit?.body as Uint8Array).toString('utf8') : requestInit?.body as string
    expect(JSON.parse(body)).toEqual({ workflowId: 'wf-1', pageSize: 1, previewSignatures, metadataPage: 1 })
  })

  it('also aborts a stalled body-bearing preview read', async () => {
    vi.useFakeTimers()
    vi.stubGlobal('fetch', vi.fn((_url: string, init: RequestInit) => new Promise((_resolve, reject) => {
      init.signal?.addEventListener('abort', () => reject(init.signal?.reason))
    })))
    const failed = expect(repairBatches.candidates('wf-1', { previewSignatures: ['issue-a'] })).rejects.toThrow(/读取超时/)
    await vi.advanceTimersByTimeAsync(30_000)
    await failed
    expect(vi.getTimerCount()).toBe(0)
  })

  it('ends a stalled detail read and permits an explicit fresh retry', async () => {
    vi.useFakeTimers()
    let aborted = false
    vi.stubGlobal('fetch', vi.fn().mockImplementationOnce((_url: string, init: RequestInit) => new Promise((_resolve, reject) => {
      init.signal?.addEventListener('abort', () => { aborted = true; reject(init.signal?.reason) })
    })).mockResolvedValueOnce({ ok: true, json: async () => ({ itemId: 'item-1' }) }))
    const failed = expect(repairBatches.item('wf-1', 'item-1')).rejects.toThrow(/读取超时/)
    await vi.advanceTimersByTimeAsync(30_000)
    await failed
    expect(aborted).toBe(true)
    expect(await repairBatches.item('wf-1', 'item-1')).toEqual({ itemId: 'item-1' })
  })

  it('clears its timer after a successful candidate read', async () => {
    vi.useFakeTimers()
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({ items: [] }) }))
    expect(await repairBatches.candidates('wf-1')).toEqual({ items: [] })
    expect(vi.getTimerCount()).toBe(0)
  })

  it('assembles every metadata page and rejects mixed source snapshots', async () => {
    const page=(number:number,digest='same')=>({inputDigest:'source',items:[],tasks:[{taskId:`task-${number}`}],repairSignatureKeys:[`signature-${number}`],facets:{nodes:[`node-${number}`],failureModes:[]},metadata:{page:number,totalPages:2,digest}})
    const urls:string[]=[]
    vi.stubGlobal('fetch',vi.fn(async(url:string)=>{urls.push(url);return {ok:true,json:async()=>page(url.includes('metadataPage=2')?2:1)}}))
    const result=await repairBatches.candidates('wf-1')
    expect(result.tasks.map(row=>row.taskId)).toEqual(['task-1','task-2'])
    expect(result.facets.nodes).toEqual(['node-1','node-2'])
    expect(urls).toHaveLength(2)
    vi.stubGlobal('fetch',vi.fn(async(url:string)=>({ok:true,json:async()=>page(url.includes('metadataPage=2')?2:1,url.includes('metadataPage=2')?'changed':'same')})))
    await expect(repairBatches.candidates('wf-1')).rejects.toThrow('建议来源已变化')
  })
  it('returns an explicitly requested candidate page without waiting for unrelated metadata pages', async () => {
    const urls:string[]=[];
    vi.stubGlobal('fetch',vi.fn(async(url:string)=>{urls.push(url);return {ok:true,json:async()=>({items:[{itemId:'current-page'}],page:{page:2,totalPages:3},metadata:{page:1,totalPages:20}})}}));
    const result=await repairBatches.candidates('wf-1',{page:2,metadataPage:1});
    expect(result.items.map(item=>item.itemId)).toEqual(['current-page']);
    expect(result.page.page).toBe(2);expect(urls).toHaveLength(1);
    expect(urls[0]).toContain('metadataPage=1');expect(urls[0]).toContain('page=2');
  })
})
