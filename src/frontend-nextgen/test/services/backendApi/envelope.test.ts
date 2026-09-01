import { isEnvelopeFailure, isEnvelopeSuccess } from '@/services/backendApi/types';
import { describe, expect, it } from '@jest/globals';

describe('backendApi envelope 成败判定', () => {
  it('code === 200000 视为成功', () => {
    expect(isEnvelopeSuccess({ code: 200000, message: 'OK', data: { a: 1 }, request_id: 'r' })).toBe(true);
  });

  it('code "200000" 字符串也视为成功(宽松匹配)', () => {
    expect(isEnvelopeSuccess({ code: '200000', message: 'OK', data: null })).toBe(true);
  });

  it('success: true 历史信封视为成功', () => {
    expect(isEnvelopeSuccess({ success: true, data: {} })).toBe(true);
  });

  it('2xx 成功段之外的 code(如 5xx)视为业务失败', () => {
    expect(isEnvelopeFailure({ code: 502201, message: 'Skill Center team creation failed', data: null })).toBe(true);
    expect(isEnvelopeSuccess({ code: 502201, message: 'x', data: null })).toBe(false);
  });

  it('2xx 成功段(201000/204000 等,后端按 HTTP status×1000 编码)视为成功', () => {
    // 创建类接口返回 201000(Created)、无数据接口返回 204000(No Content)等,均属成功。
    expect(isEnvelopeSuccess({ code: 201000, message: 'Created', data: { space_id: 64 }, request_id: 'r' })).toBe(true);
    expect(isEnvelopeSuccess({ code: 204000, message: 'No Content', data: null })).toBe(true);
    expect(isEnvelopeSuccess({ code: '201000', message: 'Created', data: null })).toBe(true);
    // 2xx 成功信封不应被判为业务失败(回归:createTeamSpace 曾把 201000 误判失败而弹错)。
    expect(isEnvelopeFailure({ code: 201000, message: 'Created', data: { space_id: 64 } })).toBe(false);
  });

  it('未知 code 仍判定为失败(不依赖枚举)', () => {
    expect(isEnvelopeFailure({ code: 999999, message: '陌生错误', data: null })).toBe(true);
  });

  it('data:null 的业务失败仍判定为失败', () => {
    expect(isEnvelopeFailure({ code: 502201, message: '创建空间数量已达上限', data: null, request_id: 'r' })).toBe(
      true,
    );
  });

  it('无信封(非对象/null)不算业务失败(非 2xx 已由 fetch 抛错)', () => {
    expect(isEnvelopeSuccess(null)).toBe(false);
    expect(isEnvelopeFailure(null)).toBe(false);
    expect(isEnvelopeSuccess(undefined)).toBe(false);
    expect(isEnvelopeFailure(undefined)).toBe(false);
  });
});
