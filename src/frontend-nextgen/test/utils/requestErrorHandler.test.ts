import { extractErrorMessage, extractFriendlyErrorMessage, formatApiPath } from '../../src/utils/requestErrorHandler';

describe('requestErrorHandler', () => {
  test('兼容常见后端错误结构', () => {
    expect(extractErrorMessage({ data: { message: '消息错误' } })).toBe('消息错误');
    expect(extractErrorMessage({ response: { data: { error: 'error 错误' } } })).toBe('error 错误');
    expect(extractErrorMessage({ data: { detail: { message: 'detail 错误' } } })).toBe('detail 错误');
    expect(extractErrorMessage({ data: { error_msg: '下划线错误' } })).toBe('下划线错误');
  });

  test('按 HTTP 状态返回友好中文文案', () => {
    expect(extractFriendlyErrorMessage({ response: { status: 401, data: {} } })).toContain('登录');
    expect(extractFriendlyErrorMessage({ response: { status: 403, data: {} } })).toContain('权限');
    expect(extractFriendlyErrorMessage({ response: { status: 429, data: {} } })).toContain('频繁');
    expect(extractFriendlyErrorMessage({ response: { status: 503, data: {} } })).toContain('服务器');
  });

  test('formatApiPath 隐藏 host 只保留 path', () => {
    expect(formatApiPath('https://teamclaw.example.com/openapi/v1/bots?a=1')).toBe('/openapi/v1/bots?a=1');
    expect(formatApiPath('/openapi/v1/bots')).toBe('/openapi/v1/bots');
  });
});

// 后端 message 优先于状态相关预设文案(5xx/4xx):body 给出可读 message 时透传,
// 仅当 body 读不到时才回退到状态预设。网络层故障(status 缺失 + "failed to fetch")回退网络文案。
describe('extractFriendlyErrorMessage — 后端 message 优先', () => {
  const envelope = (status: number, message?: string) => ({
    response: {
      status,
      data: { code: 502201, message, data: null, request_id: 'rid-0' },
    },
  });

  test('5xx 携带可读 message 时透传后端 message', () => {
    expect(extractFriendlyErrorMessage(envelope(502, 'Skill Center team creation failed'))).toBe(
      'Skill Center team creation failed',
    );
  });

  test('5xx 无可读 body 时回退「服务器暂时不可用」', () => {
    expect(extractFriendlyErrorMessage({ response: { status: 502, data: null } })).toContain('服务器');
    expect(extractFriendlyErrorMessage({ response: { status: 502, data: {} } })).toContain('服务器');
  });

  test('401 携带可读 message 时优先 message,而非「登录已过期」', () => {
    expect(extractFriendlyErrorMessage(envelope(401, 'token 已吊销'))).toBe('token 已吊销');
  });

  test('401 无 message 时回退「登录已过期」', () => {
    expect(extractFriendlyErrorMessage({ response: { status: 401, data: {} } })).toContain('登录');
  });

  test('403/429 携带可读 message 时优先 message', () => {
    expect(extractFriendlyErrorMessage(envelope(403, '禁止访问该空间'))).toBe('禁止访问该空间');
    expect(extractFriendlyErrorMessage(envelope(429, '调用太频繁了'))).toBe('调用太频繁了');
  });

  test('5xx 的 message 即便含 timeout 字样也透传(不被网络文案覆盖)', () => {
    // 后端在 body 里明确给出 message,应优先于网络预设;网络预设仅用于无 response 的纯网络故障。
    expect(extractFriendlyErrorMessage(envelope(504, '上游请求超时'))).toBe('上游请求超时');
  });

  test('纯网络层故障(无 status + "failed to fetch")回退网络文案', () => {
    expect(extractFriendlyErrorMessage({ message: 'Failed to fetch' })).toContain('网络');
  });
});
