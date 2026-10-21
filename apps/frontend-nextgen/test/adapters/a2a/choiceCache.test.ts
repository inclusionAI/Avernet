import { TransportChoiceCache } from '@avernet/a2a-chat-client';

const makeCache = () => new TransportChoiceCache();

describe('传输选择缓存（spec §2：probe-v3 可翻，locked 不翻）', () => {
  it('缓存身份=二元组：同 sessionKey 不同 endpoint 互不串', () => {
    const c = makeCache();
    c.set('sess', 'http://a:20003', 'a2a');
    expect(c.get('sess', 'http://b:20003')?.kind).toBeUndefined();
    expect(c.get('sess', 'http://a:20003')?.kind).toBe('a2a');
  });

  it('probe 结果收敛：探测通过→a2a；缺项→probe-v3', () => {
    const c = makeCache();
    expect(c.decide('sess', 'http://a:20003', true).kind).toBe('a2a');
    expect(c.decide('sess', 'http://b:20003', false).kind).toBe('probe-v3');
  });

  it('普通 probe-v3 的 reconnect 重探可翻（再次 decide 即重算）', () => {
    const c = makeCache();
    c.decide('sess', 'http://a:20003', false);               // probe-v3
    expect(c.decide('sess', 'http://a:20003', true).kind).toBe('a2a');
  });

  it('locked 本页面会话内优先于探测结果：locked 后 decide 不翻案', () => {
    const c = makeCache();
    const base = 'http://a:20003';
    c.decide('sess', base, true);
    c.markLocked('sess', base);
    expect(c.decide('sess', base, true).kind).toBe('locked-v3');
  });

  it('清空=页面会话边界（新会话 re-probe）', () => {
    const c = makeCache();
    const base = 'http://a:20003';
    c.decide('sess', base, true);
    c.markLocked('sess', base);
    c.clear();
    expect(c.decide('sess', base, true).kind).toBe('a2a');
  });

  it('重探失败把既有 a2a 项降级为 probe-v3（双向翻案=重连重探语义）', () => {
    const c = makeCache();
    const base = 'http://a:20003';
    c.decide('sess', base, true);                        // a2a
    expect(c.decide('sess', base, false).kind).toBe('probe-v3');
  });
});