import { extractLoginUrl, isAceLoginResponse } from '@/services/backendApi/aceLoginBody';
import { describe, expect, it } from '@jest/globals';

const aceBody = {
  actionType: 'LOGIN',
  buserviceErrorCode: 'USER_NOT_LOGIN',
  decisionBy: 'ACE',
  buserviceErrorMsg: 'https://login.example.com/pubLogin?goto=x',
  help: '请先完成身份验证',
};

describe('isAceLoginResponse', () => {
  it('识别完整的 ACE 登录拦截体', () => {
    expect(isAceLoginResponse(aceBody)).toBe(true);
  });

  it('缺 decisionBy 不识别(防误判,守卫业务错误体)', () => {
    expect(isAceLoginResponse({ actionType: 'LOGIN', buserviceErrorCode: 'USER_NOT_LOGIN' })).toBe(false);
  });

  it('buserviceErrorCode 不符不识别', () => {
    expect(isAceLoginResponse({ actionType: 'LOGIN', buserviceErrorCode: 'OTHER', decisionBy: 'ACE' })).toBe(false);
  });

  it('actionType 不符不识别', () => {
    expect(isAceLoginResponse({ actionType: 'LOGOUT', buserviceErrorCode: 'USER_NOT_LOGIN', decisionBy: 'ACE' })).toBe(
      false,
    );
  });

  it('业务成功信封不识别', () => {
    expect(isAceLoginResponse({ code: 200000, message: '', data: { items: [], total: 0 }, request_id: 'r' })).toBe(
      false,
    );
  });

  it('null/原始值/数组不识别', () => {
    expect(isAceLoginResponse(null)).toBe(false);
    expect(isAceLoginResponse(undefined)).toBe(false);
    expect(isAceLoginResponse('LOGIN')).toBe(false);
    expect(isAceLoginResponse([1, 2, 3])).toBe(false);
  });
});

describe('extractLoginUrl', () => {
  it('从 ACE 体取出 buserviceErrorMsg', () => {
    expect(extractLoginUrl(aceBody)).toBe(aceBody.buserviceErrorMsg);
  });

  it('非 ACE 体返回 undefined', () => {
    expect(extractLoginUrl({ code: 200000, data: {} })).toBeUndefined();
  });

  it('ACE 体但 buserviceErrorMsg 缺失返回 undefined', () => {
    expect(
      extractLoginUrl({ actionType: 'LOGIN', buserviceErrorCode: 'USER_NOT_LOGIN', decisionBy: 'ACE' }),
    ).toBeUndefined();
  });

  it('ACE 体但 buserviceErrorMsg 非字符串返回 undefined', () => {
    expect(
      extractLoginUrl({
        actionType: 'LOGIN',
        buserviceErrorCode: 'USER_NOT_LOGIN',
        decisionBy: 'ACE',
        buserviceErrorMsg: 123,
      }),
    ).toBeUndefined();
  });

  it('ACE 体但 buserviceErrorMsg 为空白串返回 undefined', () => {
    expect(
      extractLoginUrl({
        actionType: 'LOGIN',
        buserviceErrorCode: 'USER_NOT_LOGIN',
        decisionBy: 'ACE',
        buserviceErrorMsg: '   ',
      }),
    ).toBeUndefined();
  });

  it('null 返回 undefined', () => {
    expect(extractLoginUrl(null)).toBeUndefined();
  });
});
