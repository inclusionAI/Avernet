/**
 * 活体冒烟（默认跳过）：A2A_LIVE_TARGET 指向真引擎（如 simulator port-forward
 * http://127.0.0.1:20003）时才执行。验证 M1 探测/校验/wire 对真实引擎面。
 * 用例改造时勿脱离引擎实发格式——内联注释引用 wire.py/kernel_event_to_wire。
 */
import { fetchCard, validateScenarios, Scenario } from '@/adapters/a2a/card';
import { buildRpcRequest } from '@/adapters/a2a/rpc';
import { streamRpc } from '@/adapters/a2a/stream';

const TARGET = process.env.A2A_LIVE_TARGET ?? '';
const d = TARGET ? describe : describe.skip;

d('M1 活体冒烟 vs 真引擎 ' + (TARGET || '(A2A_LIVE_TARGET 未设,跳过)'), () => {
  jest.setTimeout(120_000);

  it('card 探测 + 分场景校验与活体 card 一致', async () => {
    const r = await fetchCard(`${TARGET}/.well-known/agent-card.json`);
    expect(r.card).not.toBeNull();
    const rs = validateScenarios(r.card as any, [
      Scenario.STREAM, Scenario.RECOVERY, Scenario.HITL,
    ]);
    const byName: Record<string, any> = {};
    rs.forEach((x: any) => { byName[x.scenario] = x; });
    expect(byName.stream.ok).toBe(true);      // 真引擎 streaming:true
    expect(byName.recovery.ok).toBe(true);    // 卡声明 replay
    // hitl 视引擎形态：openclaw（无 resolve 方法族）→ P0 保守不声明 → fail
    console.log('live hitl scenario:', JSON.stringify(byName.hitl));
  });

  it('SendStreamingMessage 活体全流（门放行态，真模型经 relay 回话）', async () => {
    const req = buildRpcRequest('SendStreamingMessage', {
      message: { messageId: `live-${Date.now()}`, contextId: 'e2elive2',
                 parts: [{ text: '请原样回复这五个字符:A2A通2,不要添加任何其他内容' }] },
    });
    const events: any[] = [];
    for await (const e of streamRpc(`${TARGET}/rpc`, req, {})) {
      events.push(e);
      if (e.type === 'statusUpdate' && e.metadata?.final === 'true') break;
    }
    const types = events.map((e) => e.type);
    // 真实引擎 wire：首事件 task（member 式、seq=null），终态 final marker
    expect(types[0]).not.toBe('statusUpdate');
    expect(types).toContain('artifactUpdate');
    expect(types[types.length]).not.toBeUndefined();
    const finals = events.filter((e) => e.type === 'statusUpdate' && e.metadata?.final === 'true');
    expect(finals.length).toBeGreaterThanOrEqual(1);
    const text = events.filter((e) => e.type === 'artifactUpdate')
      .map((e: any) => e.member?.artifact?.parts?.map((p: any) => p.text).join('') ?? '').join('');
    console.log('live artifact text:', JSON.stringify(text.slice(0, 60)));
    expect(text).toContain('A2A通');
  }, 240_000);

  it('GetTask(未门控只读) 假 taskId → 活体 -32000 not_found_or_expired reason', async () => {
    const req = buildRpcRequest('GetTask', { taskId: 'live-nonexistent' });
    await expect((async () => {
      for await (const _ of streamRpc(`${TARGET}/rpc`, req, {})) { /* drain */ }
    })()).rejects.toMatchObject({
      failure: { kind: 'rpc', rpcError: { code: -32000,
        data: { reason: 'not_found_or_expired' } } },
    });
  });
});