/** @jest-environment node */
// 通道 A(global-error-notify-dedup):业务失败 2xx→reject+投递;HTTP/网络错误→投递+上抛;已报告不重复;skip 透传。
// 未登录处置(external-oauth-login):oauth 策略下未登录失败 → 登录弹窗信号 + 静默上抛,不投递逐条错误 toast。
import {
  RequestProtocolError,
  reportProtocolFailure,
  teamclawErrorHandler,
  teamclawResponseInterceptor,
} from '@/requestConfig';
import { AceLoginRedirectError } from '@/services/backendApi/httpClient';
import { useErrorNotifyStore } from '@/stores/errorNotifyStore';
import { useExternalAuthStore } from '@/stores/externalAuthStore';
import { useLoginRedirectStore } from '@/stores/loginRedirectStore';
import { useLoginStrategyStore } from '@/stores/loginStrategyStore';
import { afterEach, beforeEach, describe, expect, it } from '@jest/globals';

beforeEach(() => useErrorNotifyStore.getState().reset());

describe('teamclawResponseInterceptor(通道 A 业务失败信封)', () => {
  it('HTTP 2xx + 业务失败(code != 200000)→ 投递默认提示 + 抛 RequestProtocolError', () => {
    const response = {
      status: 200,
      config: { url: '/api/x', operation: 'op' },
      data: { code: 500001, message: '已达上限' },
    };

    expect(() => teamclawResponseInterceptor(response)).toThrow(RequestProtocolError);

    const [item] = useErrorNotifyStore.getState().queue;
    expect(item.toastKey).toBe('req:/api/x:op');
    expect(item.message).toBe('已达上限');
    expect(item.apiPath).toBe('/api/x');
  });

  it('成功信封(code === 200000)→ 透传、不投递', () => {
    const response = { status: 200, config: { url: '/api/x' }, data: { code: 200000, data: { id: 1 } } };
    expect(teamclawResponseInterceptor(response)).toBe(response);
    expect(useErrorNotifyStore.getState().queue).toHaveLength(0);
  });

  it('skipErrorHandler → 透传、不投递', () => {
    const response = {
      status: 200,
      config: { url: '/api/x', skipErrorHandler: true },
      data: { code: 500001, message: 'x' },
    };
    expect(teamclawResponseInterceptor(response)).toBe(response);
    expect(useErrorNotifyStore.getState().queue).toHaveLength(0);
  });

  it('非信封数据(string)→ 透传、不投递', () => {
    const response = { status: 200, config: { url: '/api/x' }, data: 'plain' };
    expect(teamclawResponseInterceptor(response)).toBe(response);
    expect(useErrorNotifyStore.getState().queue).toHaveLength(0);
  });
});

describe('teamclawErrorHandler(通道 A HTTP/网络错误)', () => {
  it('原始 HTTP 错误 → 投递默认提示 + 抛 RequestProtocolError(挂 toastKey/alreadyHandled)', () => {
    const error = {
      config: { url: '/api/y', operation: 'get' },
      response: { status: 502, data: { message: '网关错误' } },
    };

    expect(() => teamclawErrorHandler(error)).toThrow(RequestProtocolError);

    const [item] = useErrorNotifyStore.getState().queue;
    expect(item.message).toBe('网关错误');
    expect(item.toastKey).toBe('req:/api/y:get');
  });

  it('已被拦截器报告的 RequestProtocolError → 不重复投递,原样上抛', () => {
    let reported: RequestProtocolError | undefined;
    try {
      teamclawResponseInterceptor({
        status: 200,
        config: { url: '/api/z', operation: 'op' },
        data: { code: 500001, message: 'm' },
      });
    } catch (e) {
      reported = e as RequestProtocolError;
    }
    expect(reported).toBeInstanceOf(RequestProtocolError);
    const before = useErrorNotifyStore.getState().queue.length;

    expect(() => teamclawErrorHandler(reported)).toThrow(RequestProtocolError);
    expect(useErrorNotifyStore.getState().queue.length).toBe(before); // 不重复投递
  });

  it('skipErrorHandler → 不投递、不抛错(umi 以原 error reject)', () => {
    const error = { config: { url: '/api/y' }, response: { status: 500, data: {} } };
    expect(() => teamclawErrorHandler(error, { skipErrorHandler: true })).not.toThrow();
    expect(useErrorNotifyStore.getState().queue).toHaveLength(0);
  });
});

describe('reportProtocolFailure(投递 + 抛错)', () => {
  it('投递 errorNotifyStore 并抛 RequestProtocolError(挂 toastKey/alreadyHandled=true)', () => {
    expect(() => reportProtocolFailure({ apiPath: '/api/r', message: 'boom', operation: 'op' })).toThrow(
      RequestProtocolError,
    );

    let caught: unknown;
    try {
      reportProtocolFailure({ apiPath: '/api/r', message: 'boom', operation: 'op' });
    } catch (e) {
      caught = e;
    }
    expect(caught).toBeInstanceOf(RequestProtocolError);
    const err = caught as RequestProtocolError;
    expect(err.toastKey).toBe('req:/api/r:op');
    expect(err.alreadyHandled).toBe(true);
    expect(err.message).toBe('boom');

    expect(useErrorNotifyStore.getState().queue[0].toastKey).toBe('req:/api/r:op');
  });
});

describe('未登录处置(oauth-provider 策略,通道 A)', () => {
  const resetExternalAuth = (): void =>
    useExternalAuthStore.setState({ status: 'unknown', user: null, error: null, isCheckingAuth: false });

  beforeEach(() => {
    useLoginStrategyStore.getState().setLoginStrategy('oauth-provider');
    useLoginRedirectStore.getState().reset();
    useErrorNotifyStore.getState().reset();
    resetExternalAuth();
  });

  afterEach(() => {
    useLoginStrategyStore.getState().setLoginStrategy('ace-gateway');
    useLoginRedirectStore.getState().reset();
    resetExternalAuth();
  });

  it('HTTP 2xx + 网关误包 40100 信封 → 弹窗信号 + 静默抛 AceLoginRedirectError,不入错误提示队列', () => {
    const response = {
      status: 200,
      config: { url: '/api/x', operation: 'op' },
      data: { code: 40100, message: 'Authentication is required', request_id: 'r' },
    };

    expect(() => teamclawResponseInterceptor(response)).toThrow(AceLoginRedirectError);

    expect(useLoginRedirectStore.getState().pendingLogin).toEqual({ mode: 'prompt' });
    expect(useErrorNotifyStore.getState().queue).toHaveLength(0);
  });

  it('原始 HTTP 401 错误 → 弹窗信号 + 静默抛 AceLoginRedirectError,不入错误提示队列', () => {
    const error = {
      config: { url: '/api/y', operation: 'op' },
      response: { status: 401, data: { message: 'unauthorized' } },
    };

    expect(() => teamclawErrorHandler(error)).toThrow(AceLoginRedirectError);

    expect(useLoginRedirectStore.getState().pendingLogin).toEqual({ mode: 'prompt' });
    expect(useErrorNotifyStore.getState().queue).toHaveLength(0);
  });

  it('已确认未登录 + 非 401 失败(500)→ 静默抛 RequestProtocolError(alreadyHandled),不入错误提示队列', () => {
    useExternalAuthStore.getState().setUnauthenticated();
    const error = {
      config: { url: '/api/z', operation: 'op' },
      response: { status: 500, data: { message: '服务异常' } },
    };

    let caught: unknown;
    try {
      teamclawErrorHandler(error);
    } catch (e) {
      caught = e;
    }

    expect(caught).toBeInstanceOf(RequestProtocolError);
    expect((caught as RequestProtocolError).alreadyHandled).toBe(true);
    expect(useErrorNotifyStore.getState().queue).toHaveLength(0);
    expect(useLoginRedirectStore.getState().pendingLogin).toBeUndefined();
  });

  it('ace-gateway(默认策略)→ 行为不变:照常投递默认提示', () => {
    useLoginStrategyStore.getState().setLoginStrategy('ace-gateway');
    const response = {
      status: 200,
      config: { url: '/api/x', operation: 'op' },
      data: { code: 40100, message: 'Authentication is required', request_id: 'r' },
    };

    expect(() => teamclawResponseInterceptor(response)).toThrow(RequestProtocolError);

    expect(useLoginRedirectStore.getState().pendingLogin).toBeUndefined();
    expect(useErrorNotifyStore.getState().queue).toHaveLength(1);
  });
});
