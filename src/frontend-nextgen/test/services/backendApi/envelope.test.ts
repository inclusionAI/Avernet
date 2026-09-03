import {
  isEnvelopeFailure,
  isEnvelopeSuccess,
  isEnvelopeSuccessAnyDialect,
  isEnvelopeUnauthenticated,
} from '@/services/backendApi/types';
import { describe, expect, it } from '@jest/globals';

/**
 * 双方言零冲突论证（design 决策 1）以测试常量固化：
 * 6 位方言（python backend，HTTP×1000+子码）成功段 [200000, 300000)；
 * 5 位方言（BCS，HTTP×100+子码）成功段 [20000, 30000)。二者无交集。
 */
const SIX_DIGIT_SUCCESS_MIN = 200000;
const SIX_DIGIT_SUCCESS_MAX_EXCLUSIVE = 300000;
const FIVE_DIGIT_SUCCESS_MIN = 20000;
const FIVE_DIGIT_SUCCESS_MAX_EXCLUSIVE = 30000;
/** BCS error.rs 全量错误码映射（含 5 位方言错误域下限），均不得落入任一成功段。 */
const BCS_ERROR_CODES = [40000, 40100, 40300, 40400, 40900, 41000, 41300, 42200, 42900, 50000, 50200] as const;

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

describe('backendApi envelope 双方言判定(6 位全局紧致 / 6∪5 位并集专供 BCS 域消费方)', () => {
  // 全局谓词保持 python 6 位方言:BCS 5 位成功码属跨域误码,python 域必须拒绝
  // (catalog / 私有 Session 紧致性锁定测试依赖此语义,见 change design 决策 1 修正)。
  it('isEnvelopeSuccess(全局)拒绝 BCS 5 位成功码(20000/20100/20200)', () => {
    for (const code of [20000, 20100, 20200]) {
      expect(isEnvelopeSuccess({ code, message: 'OK', data: null })).toBe(false);
      expect(isEnvelopeFailure({ code, message: 'OK', data: null })).toBe(true);
    }
  });

  // 并集谓词仅供同时服务两种部署的消费方(auth 协议边界)使用。
  it('isEnvelopeSuccessAnyDialect 接受 BCS 5 位 2xx 段(20000=OK/20100=Created/20200=Accepted)', () => {
    expect(isEnvelopeSuccessAnyDialect({ code: 20000, message: 'OK', data: { providers: [] }, request_id: 'r' })).toBe(
      true,
    );
    expect(isEnvelopeSuccessAnyDialect({ code: '20000', message: 'OK', data: null })).toBe(true);
    expect(isEnvelopeSuccessAnyDialect({ code: 20100, message: 'Created', data: { session_id: 's' } })).toBe(true);
    expect(isEnvelopeSuccessAnyDialect({ code: 20200, message: 'Accepted', data: null })).toBe(true);
  });

  it('isEnvelopeSuccessAnyDialect 沿用 6 位成功段(200000 系)', () => {
    expect(isEnvelopeSuccessAnyDialect({ code: 200000, message: 'OK', data: null })).toBe(true);
    expect(isEnvelopeSuccessAnyDialect({ code: 201000, message: 'Created', data: null })).toBe(true);
  });

  it('isEnvelopeSuccessAnyDialect 仍拒绝双方言全部错误码与非 2xx 段', () => {
    // BCS error.rs 全量错误码 + 5 位 1xx/3xx/9xx 边界 + 6 位错误段。
    for (const code of [...BCS_ERROR_CODES, 19999, 30000, 99999, 400000, 502201]) {
      expect(isEnvelopeSuccessAnyDialect({ code, message: 'x', data: null })).toBe(false);
    }
    expect(isEnvelopeSuccessAnyDialect(null)).toBe(false);
    expect(isEnvelopeSuccessAnyDialect(undefined)).toBe(false);
  });

  it('6 位方言全局判定不受并集谓词影响(既有 python 域回归)', () => {
    expect(isEnvelopeSuccess({ code: 200000, message: 'OK', data: null })).toBe(true);
    expect(isEnvelopeFailure({ code: 502201, message: 'x', data: null })).toBe(true);
  });

  // 零冲突论证固化:改码表/扩段时此用例必须同步复审(见 change design 决策 1)。
  it('两方言成功段无交集,且全部已知错误码不落任一成功段', () => {
    expect(FIVE_DIGIT_SUCCESS_MAX_EXCLUSIVE).toBeLessThanOrEqual(SIX_DIGIT_SUCCESS_MIN);
    expect(FIVE_DIGIT_SUCCESS_MIN).toBeGreaterThanOrEqual(10000);
    for (const code of BCS_ERROR_CODES) {
      const inFive = code >= FIVE_DIGIT_SUCCESS_MIN && code < FIVE_DIGIT_SUCCESS_MAX_EXCLUSIVE;
      const inSix = code >= SIX_DIGIT_SUCCESS_MIN && code < SIX_DIGIT_SUCCESS_MAX_EXCLUSIVE;
      expect(inFive).toBe(false);
      expect(inSix).toBe(false);
    }
  });
});

/**
 * 未登录信封判定(external-oauth-login「未登录静默与统一登录处置」):双方言并集,
 * BCS 显式 error_code 形态 + 网关误包 401 段形态(python 6 位 401000–401999 / BCS 5 位 40100–40199)。
 */
describe('backendApi envelope 未登录判定(isEnvelopeUnauthenticated)', () => {
  it('BCS 显式形态 data.error_code=unauthenticated 判为未登录(既有 401 反应口契约)', () => {
    expect(
      isEnvelopeUnauthenticated({
        code: 40100,
        message: 'Authentication is required',
        data: { error_code: 'unauthenticated' },
      }),
    ).toBe(true);
  });

  it('网关误包形态:code 落 401 段即未登录(5 位 40100–40199 / 6 位 401000–401999)', () => {
    expect(isEnvelopeUnauthenticated({ code: 40100, message: 'x', data: null })).toBe(true);
    expect(isEnvelopeUnauthenticated({ code: 40199, message: 'x', data: null })).toBe(true);
    expect(isEnvelopeUnauthenticated({ code: 401000, message: '未登录', data: null })).toBe(true);
    expect(isEnvelopeUnauthenticated({ code: 401999, message: '未登录', data: null })).toBe(true);
    expect(isEnvelopeUnauthenticated({ code: '40100', message: 'x', data: null })).toBe(true); // 字符串宽松匹配
  });

  it('非 401 段错误码不判为未登录(403/404/5xx 等)', () => {
    expect(isEnvelopeUnauthenticated({ code: 40300, message: 'forbidden', data: { error_code: 'forbidden' } })).toBe(
      false,
    );
    expect(isEnvelopeUnauthenticated({ code: 403000, message: 'forbidden', data: null })).toBe(false);
    expect(isEnvelopeUnauthenticated({ code: 502201, message: 'x', data: null })).toBe(false);
    expect(isEnvelopeUnauthenticated({ code: 50000, message: 'x', data: null })).toBe(false);
  });

  it('成功信封与非信封数据不判为未登录', () => {
    expect(isEnvelopeUnauthenticated({ code: 200000, message: 'OK', data: { a: 1 } })).toBe(false);
    expect(isEnvelopeUnauthenticated({ code: 20000, message: 'OK', data: null })).toBe(false); // BCS 5 位成功段
    expect(isEnvelopeUnauthenticated(null)).toBe(false);
    expect(isEnvelopeUnauthenticated(undefined)).toBe(false);
    expect(isEnvelopeUnauthenticated('plain')).toBe(false);
  });
});
