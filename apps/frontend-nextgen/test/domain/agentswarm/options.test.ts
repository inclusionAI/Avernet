import { validateOptions, normalizeOptions, hasAnyOption, OPTION_FIELDS } from '@/domain/agentswarm/options';

test('an empty options object is valid — every field means "use the default"', () => {
  expect(validateOptions({})).toEqual([]);
  expect(validateOptions(null)).toEqual([]);
  expect(validateOptions(undefined)).toEqual([]);
});

test('each option field refuses out-of-range values with its own label', () => {
  for (const field of OPTION_FIELDS) {
    const tooLow = validateOptions({ [field.key]: field.min - 1 });
    const tooHigh = validateOptions({ [field.key]: field.max + 1 });
    expect(tooLow).toHaveLength(1);
    expect(tooHigh).toHaveLength(1);
    expect(tooLow[0].message).toContain(field.label);
  }
});

test('bounds follow the gateway, except a deliberately tighter turn budget', () => {
  const byKey = Object.fromEntries(OPTION_FIELDS.map((field) => [field.key, field]));
  expect(byKey.max_total_agents.max).toBe(128);
  expect(byKey.max_width.max).toBe(8);
  expect(byKey.max_attempts.max).toBe(3);
  // The gateway clamps max_turns at 999, but this surface caps it at 128: a
  // larger budget only matters for runs parked on human authorization.
  expect(byKey.max_turns.max).toBe(128);
});

test('a count field refuses a fraction', () => {
  expect(validateOptions({ max_width: 2.5 })[0].message).toContain('整数');
});

test('a seconds field accepts a fraction', () => {
  expect(validateOptions({ per_attempt_timeout_seconds: 12.5 })).toEqual([]);
});

test('a non-numeric entry is refused rather than coerced', () => {
  expect(validateOptions({ max_width: Number.NaN })[0].message).toContain('数字');
});

test('an attempt timeout longer than the slice is refused', () => {
  const issues = validateOptions({ slice_max_seconds: 60, per_attempt_timeout_seconds: 120 });
  expect(issues).toHaveLength(1);
  expect(issues[0].key).toBe('per_attempt_timeout_seconds');
});

test('an attempt timeout within the slice is accepted', () => {
  expect(validateOptions({ slice_max_seconds: 120, per_attempt_timeout_seconds: 60 })).toEqual([]);
});

test('blank values are omitted from the payload so the server keeps its defaults', () => {
  expect(normalizeOptions({ max_width: null, max_turns: undefined })).toBeUndefined();
  expect(normalizeOptions({ max_width: 3, max_turns: null })).toEqual({ max_width: 3 });
  expect(normalizeOptions(null)).toBeUndefined();
});

test('a model name alone counts as customization', () => {
  expect(hasAnyOption({ model_name: 'GLM-5' })).toBe(true);
  expect(hasAnyOption({ model_name: null })).toBe(false);
  expect(hasAnyOption({})).toBe(false);
});