import { AgentswarmRequestError } from '@/services/agentswarm/http';
import { explainSubmitRefusal } from '@/services/agentswarm/refusals';

const refusal = (kind: AgentswarmRequestError['kind'], message: string, status?: number) =>
  new AgentswarmRequestError(message, kind, status);

/**
 * The simulator gateway flattens the backend's `{"error": ...}` detail into a
 * generic status phrase, so the explanation can only come from the status.
 * Verified against live `POST /runs`: 400 "Bad Request", 422 "Unprocessable
 * Entity" — neither carries the service's own wording.
 */
test('a gateway-flattened 422 still yields the actionable identity message', () => {
  const result = explainSubmitRefusal(refusal('identity_source_invalid', 'Unprocessable Entity', 422));
  expect(result.message).toContain('改选其他 Bot');
  expect(result.message).not.toBe('Unprocessable Entity');
});

test('a gateway-flattened 400 still yields the choose-identity message', () => {
  const result = explainSubmitRefusal(refusal('identity_source_missing', 'Bad Request', 400));
  expect(result.message).toContain('身份 Bot');
  expect(result.message).not.toBe('Bad Request');
});

test('a missing identity source tells the user to choose one and stays retryable', () => {
  const result = explainSubmitRefusal(refusal('identity_source_missing', 'raw gateway text', 400));
  expect(result.message).toContain('身份 Bot');
  expect(result.message).not.toBe('raw gateway text');
  expect(result.retryable).toBe(true);
  expect(result.verifyBeforeRetry).toBe(false);
});

test('an unusable identity source suggests choosing another', () => {
  const result = explainSubmitRefusal(refusal('identity_source_invalid', 'raw gateway text', 422));
  expect(result.message).toContain('改选其他 Bot');
  expect(result.retryable).toBe(true);
});

test('an unconfirmed outcome advises verifying before resubmitting', () => {
  const result = explainSubmitRefusal(refusal('unconfirmed', '网关错误', 502));
  expect(result.message).toContain('不要重复提交');
  expect(result.verifyBeforeRetry).toBe(true);
  expect(result.retryable).toBe(false);
});

test('the three identity outcomes produce three distinct messages', () => {
  const a = explainSubmitRefusal(refusal('identity_source_missing', 'x', 400)).message;
  const b = explainSubmitRefusal(refusal('identity_source_invalid', 'x', 422)).message;
  const c = explainSubmitRefusal(refusal('unconfirmed', 'x', 502)).message;
  expect(new Set([a, b, c]).size).toBe(3);
});

test('a local validation refusal is retryable once corrected', () => {
  const result = explainSubmitRefusal(new Error('请选择身份 Bot'));
  expect(result).toMatchObject({ retryable: true, verifyBeforeRetry: false, message: '请选择身份 Bot' });
});

test('an unrecognized failure stays generic and does not claim the run exists', () => {
  const result = explainSubmitRefusal('boom');
  expect(result.verifyBeforeRetry).toBe(false);
  expect(result.message).not.toContain('运行已创建');
});