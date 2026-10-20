/**
 * B12 — classifyCancel 四态归类器单元测试（纯函数零网络）。
 * 事实源：engine router.py `_cancel_task`（~527-562）真实响应形状——
 *   成功 {task:{status:{state}},engineAbort}；重复 cancel error
 *   {reason:'task_terminal',state,already_terminal}；not_found_or_expired；
 *   请求流断 = typed failure。
 */
import {
  classifyCancel,
  CANCEL_UI_TEXT,
  type CancelOutcomeKind,
} from '@/adapters/a2a/cancelOutcomes';

/** engine router._cancel_task 成功信封（kernel run_snapshot 口径） */
const cancelResult = (state: string, engineAbort?: string) => ({
  task: { id: 't1', contextId: 'c1', status: { state }, metadata: {} },
  ...(engineAbort !== undefined ? { engineAbort } : {}),
});

describe('classifyCancel — §4.3 四态归类', () => {
  it('row cancelled-clean：成功 + state=CANCELED + engineAbort=confirmed', () => {
    expect(classifyCancel({ ok: true, result: cancelResult('TASK_STATE_CANCELED', 'confirmed') }))
      .toEqual({ kind: 'cancelled-clean', engineAbort: 'confirmed' });
  });

  it('row cancelled-unconfirmed：CANCELED + engineAbort 非 confirmed（refused/timeout/'
    + 'unavailable/not_found 或缺失全归此行）', () => {
    for (const ab of ['refused', 'timeout', 'unavailable', 'not_found', undefined]) {
      expect(classifyCancel({ ok: true, result: cancelResult('TASK_STATE_CANCELED', ab) }))
        .toEqual({ kind: 'cancelled-unconfirmed',
          ...(ab !== undefined ? { engineAbort: ab } : {}) });
    }
  });

  it('row cancel-rejected（rpc 面）：显式拒绝——repeat task_terminal 带 state 者'
    + ' 原样附录终局', () => {
    expect(classifyCancel({
      rpcError: {
        code: -32000, message: 'task already terminal (TASK_STATE_COMPLETED)',
        data: { reason: 'task_terminal', state: 'TASK_STATE_COMPLETED', already_terminal: true },
      },
    })).toEqual({ kind: 'cancel-rejected', terminalState: 'TASK_STATE_COMPLETED' });
  });

  it('row cancel-rejected-unknown（F2/§3.1）：not_found_or_expired 独立行——'
    + ' ' + '查无任务不构成底层执行已停止的证明（禁令核销=begin 保持），'
    + 'UI 文案与 cancel-rejected 同', () => {
    const outcome = classifyCancel({
      rpcError: {
        code: -32000, message: 'task not found or expired',
        data: { reason: 'not_found_or_expired' },
      },
    });
    expect(outcome).toEqual({ kind: 'cancel-rejected-unknown' });
    // 同文案（用户面无差）：typed 分行只为 provider 侧禁令核销不放行
    expect(CANCEL_UI_TEXT['cancel-rejected-unknown'])
      .toBe(CANCEL_UI_TEXT['cancel-rejected']);
  });

  it('row already-terminal CANCELED 特例（repeat cancel 命中已完成态取消）：'
    + 'engineAbort 该路径永不可知 → unconfirmed（不得自我安慰成 clean）', () => {
    expect(classifyCancel({
      rpcError: {
        code: -32000, message: 'task already terminal (TASK_STATE_CANCELED)',
        data: { reason: 'task_terminal', state: 'TASK_STATE_CANCELED', already_terminal: true },
      },
    })).toEqual({ kind: 'cancelled-unconfirmed', terminalState: 'TASK_STATE_CANCELED' });
  });

  it('row cancel-unknown：请求流断（typed transport/protocol/auth）——结局不可知', () => {
    expect(classifyCancel({ failure: { kind: 'transport' } }))
      .toEqual({ kind: 'cancel-unknown' });
    expect(classifyCancel({ failure: { kind: 'protocol' } }))
      .toEqual({ kind: 'cancel-unknown' });
    // 零信息（无 result 无 error 无 failure）：不臆断，同 unknown
    expect(classifyCancel({})).toEqual({ kind: 'cancel-unknown' });
  });

  it('防御位：成功信封但 state 非 CANCELED → rejected 附录终局', () => {
    expect(classifyCancel({ ok: true, result: cancelResult('TASK_STATE_COMPLETED') }))
      .toEqual({ kind: 'cancel-rejected', terminalState: 'TASK_STATE_COMPLETED' });
  });

  it('typed rpc throw 面：failure.rpcError 随行也归 rpc 分支（rpcOnce 的 throw 形状）', () => {
    expect(classifyCancel({
      failure: {
        kind: 'rpc',
        rpcError: {
          code: -32000, message: 'task already terminal (TASK_STATE_FAILED)',
          data: { reason: 'task_terminal', state: 'TASK_STATE_FAILED', already_terminal: true },
        },
      },
    })).toEqual({ kind: 'cancel-rejected', terminalState: 'TASK_STATE_FAILED' });
  });

  it('CANCEL_UI_TEXT 单源：五键齐备且非空（B13 UI 复用面；F2 增 rejected-unknown 行）', () => {
    expect(Object.keys(CANCEL_UI_TEXT).sort()).toEqual([
      'cancel-rejected', 'cancel-rejected-unknown', 'cancel-unknown',
      'cancelled-clean', 'cancelled-unconfirmed',
    ]);
    for (const kind of Object.keys(CANCEL_UI_TEXT) as CancelOutcomeKind[]) {
      expect(typeof CANCEL_UI_TEXT[kind]).toBe('string');
      expect(CANCEL_UI_TEXT[kind].length).toBeGreaterThan(0);
    }
  });
});
