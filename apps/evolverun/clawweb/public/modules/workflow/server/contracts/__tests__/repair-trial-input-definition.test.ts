import { describe, expect, it } from 'vitest';
import { normalizeTrialInputDefinition as normalize } from '../repair-trial-input-definition';
import { validateTrialInput } from '../../services/repair-trial-input';

describe('normalizeTrialInputDefinition', () => {
  it('normalizes the deployed field-map shape without inventing properties named type or required', () => {
    expect(normalize({ schema: { userId: { type: 'string', description: '账户', sensitive: true }, dutyWorkId: { type: 'integer' }, taskId: { enum: ['a', 'b'] } }, requiredParams: ['userId'] })).toEqual({
      inputDefinition: 'defined', inputSchema: { type: 'object', required: ['userId'], properties: {
        userId: { type: 'string', description: '账户', sensitive: true }, dutyWorkId: { type: 'integer' }, taskId: { enum: ['a', 'b'] },
      } },
    });
    expect(normalize({ schema: { type: { type: 'string' }, required: { type: 'boolean' } } }).inputSchema?.properties).toEqual({ type: { type: 'string' }, required: { type: 'boolean' } });
  });
  it('preserves JSON Schema constraints and merges separate required fields', () => {
    const schema = { type: 'object', additionalProperties: false, required: ['query'], properties: { query: { type: 'string', minLength: 2 } } };
    expect(normalize({ schema, requiredParams: ['userId', 'query'] }).inputSchema).toEqual({ ...schema, required: ['query', 'userId'], properties: { ...schema.properties, userId: { type: 'string' } } });
    expect(schema.properties).toEqual({ query: { type: 'string', minLength: 2 } });
  });
  it('distinguishes business fields named properties/required/type from the JSON Schema wrapper', () => {
    const businessFields = { properties: { type: 'string', description: '业务属性' }, required: { type: 'boolean' }, type: { type: 'string' } };
    expect(normalize({ schema: businessFields }).inputSchema?.properties).toEqual(businessFields);
    const schema = { type: 'object', required: ['properties'], properties: businessFields };
    expect(normalize({ schema }).inputSchema).toEqual(schema);
    expect(normalize({ schema: { properties: { enum: ['a', 'b'] } } }).inputSchema?.properties).toEqual({ properties: { enum: ['a', 'b'] } });
  });
  it('presents required-only inputs with explicit input requirements overriding legacy ones', () => {
    expect(normalize({ requiredParams: ['taskId'] }, ['oldId']).inputSchema).toMatchObject({ required: ['taskId'], properties: { taskId: { type: 'string' } } });
    expect(normalize(undefined, ['userId']).inputSchema).toMatchObject({ required: ['userId'], properties: { userId: { type: 'string' } } });
  });
  it('uses actual root params values as defaults, retaining false, zero, empty strings and structured values', () => {
    expect(normalize({ params: { enabled: false, count: 0, query: '', tags: ['a'], context: { id: 1 } } }).inputSchema?.properties).toEqual({
      enabled: { type: 'boolean', default: false }, count: { type: 'number', default: 0 }, query: { type: 'string', default: '' },
      tags: { type: 'array', default: ['a'] }, context: { type: 'object', default: { id: 1 } },
    });
    expect(normalize({ schema: { count: { type: 'integer', default: 0 } }, params: { count: 5 } }).inputSchema?.properties).toEqual({ count: { type: 'integer', default: 0 } });
  });
  it('distinguishes missing declarations from explicit empty declarations', () => {
    for (const input of [undefined, null, { schema: null }, { schema: undefined, params: undefined }]) expect(normalize(input)).toEqual({ inputSchema: null, inputDefinition: 'missing' });
    for (const input of [{}, { schema: {} }, { schema: { type: 'object' } }, { schema: { type: 'object', properties: {} } }, { requiredParams: [] }, { params: {} }]) expect(normalize(input).inputDefinition).toBe('none');
  });
  it.each([
    { type: 'object', additionalProperties: true },
    { type: 'object', additionalProperties: { type: 'string' } },
    { type: 'object', additionalProperties: { type: 'string' }, minProperties: 1 },
    { type: 'object', minProperties: 1 },
    { minProperties: 1 },
  ])('keeps an explicitly open or constrained object available for business input: %j', schema => {
    expect(normalize({ schema })).toEqual({ inputDefinition: 'defined', inputSchema: { ...schema, type: 'object', properties: {} } });
  });
  it('keeps an explicitly closed empty object parameterless without changing schema constraints', () => {
    const schema = { type: 'object', additionalProperties: false };
    expect(normalize({ schema })).toEqual({ inputDefinition: 'none', inputSchema: { ...schema, properties: {} } });
    expect(normalize({ schema: {} }).inputDefinition).toBe('none');
  });
  it.each([
    { dependencies: { creditCard: ['billingAddress'] } },
    { dependencies: { creditCard: { required: ['billingAddress'] } } },
    { dependencies: { type: ['billingAddress'] } },
  ])('retains root dependencies without type for actual non-coercing input validation: %j', schema => {
    const normalized = normalize({ schema });
    expect(normalized).toEqual({ inputDefinition: 'defined', inputSchema: { ...schema, type: 'object', properties: {} } });
    const trigger = Object.keys(schema.dependencies)[0];
    expect(() => validateTrialInput({ [trigger]: 'card' }, normalized.inputSchema)).toThrow(/参数校验失败/);
    const valid = { [trigger]: 'card', billingAddress: 'address' };
    expect(() => validateTrialInput(valid, normalized.inputSchema)).not.toThrow();
    expect(valid).toEqual({ [trigger]: 'card', billingAddress: 'address' });
  });
  it('keeps conditional root branches without type and validates both sides', () => {
    const schema = { if: { required: ['creditCard'] }, then: { required: ['billingAddress'] }, else: { required: ['customerId'] } };
    const normalized = normalize({ schema });
    expect(normalized).toEqual({ inputDefinition: 'defined', inputSchema: { ...schema, type: 'object', properties: {} } });
    expect(() => validateTrialInput({ creditCard: 'card' }, normalized.inputSchema)).toThrow(/参数校验失败/);
    expect(() => validateTrialInput({}, normalized.inputSchema)).toThrow(/参数校验失败/);
    expect(() => validateTrialInput({ creditCard: 'card', billingAddress: 'address' }, normalized.inputSchema)).not.toThrow();
    expect(() => validateTrialInput({ customerId: 'customer' }, normalized.inputSchema)).not.toThrow();
  });
  it.each([
    { enum: [{ method: 'manual' }] },
    { const: { method: 'manual' } },
    { if: true, then: { const: { method: 'manual' } }, else: false },
  ])('preserves root object enum/const and boolean condition branches: %j', schema => {
    const normalized = normalize({ schema });
    expect(normalized).toEqual({ inputDefinition: 'defined', inputSchema: { ...schema, type: 'object', properties: {} } });
    expect(() => validateTrialInput({ method: 'auto' }, normalized.inputSchema)).toThrow(/参数校验失败/);
    expect(() => validateTrialInput({ method: 'manual' }, normalized.inputSchema)).not.toThrow();
  });
  it('preserves root property-name constraints without type', () => {
    const schema = { propertyNames: { pattern: '^[a-z]+$' } };
    const normalized = normalize({ schema });
    expect(normalized).toEqual({ inputDefinition: 'defined', inputSchema: { ...schema, type: 'object', properties: {} } });
    expect(() => validateTrialInput({ INVALID: 'name' }, normalized.inputSchema)).toThrow(/参数校验失败/);
    expect(() => validateTrialInput({ valid: 'name' }, normalized.inputSchema)).not.toThrow();
  });
  it('keeps business fields named dependencies or const when their values are valid field descriptors', () => {
    const schema = { dependencies: { type: 'string', description: '业务依赖' }, const: { enum: ['manual', 'auto'] }, if: { type: 'string' } };
    const normalized = normalize({ schema });
    expect(normalized).toEqual({ inputDefinition: 'defined', inputSchema: { type: 'object', properties: schema } });
    expect(() => validateTrialInput({ dependencies: 'business', const: 'manual' }, normalized.inputSchema)).not.toThrow();
    expect(() => validateTrialInput({ dependencies: 42, const: 'other' }, normalized.inputSchema)).toThrow(/参数校验失败/);
  });
  it.each(['additionalProperties', 'not'])('preserves a required business field named %s with its original boolean type', key => {
    const schema = { [key]: { type: 'boolean' } };
    const normalized = normalize({ schema, requiredParams: [key] });
    expect(normalized).toEqual({ inputDefinition: 'defined', inputSchema: { type: 'object', properties: schema, required: [key] } });
    expect(normalize({ schema }, [key])).toEqual(normalized);
    expect(() => validateTrialInput({ [key]: false }, normalized.inputSchema)).not.toThrow();
    expect(() => validateTrialInput({ [key]: 'false' }, normalized.inputSchema)).toThrow(/参数校验失败/);
    expect(() => validateTrialInput({}, normalized.inputSchema)).toThrow(/参数校验失败/);
  });
  it('uses the required-field evidence for the whole field map without dropping neighboring keyword-named fields', () => {
    const schema = { additionalProperties: { type: 'boolean' }, not: { type: 'integer' }, userId: { type: 'string' } };
    const normalized = normalize({ schema, requiredParams: ['additionalProperties'] });
    expect(normalized.inputSchema).toEqual({ type: 'object', properties: schema, required: ['additionalProperties'] });
    expect(() => validateTrialInput({ additionalProperties: false, not: 0, userId: 'user' }, normalized.inputSchema)).not.toThrow();
    expect(() => validateTrialInput({ additionalProperties: false, not: '0' }, normalized.inputSchema)).toThrow(/参数校验失败/);
  });
  it('retains the root meaning of untyped additionalProperties without required-field evidence', () => {
    const schema = { additionalProperties: { type: 'boolean' } };
    const normalized = normalize({ schema });
    expect(normalized.inputSchema).toEqual({ ...schema, type: 'object', properties: {} });
    expect(() => validateTrialInput({ flag: false }, normalized.inputSchema)).not.toThrow();
    expect(() => validateTrialInput({ flag: 'false' }, normalized.inputSchema)).toThrow(/参数校验失败/);
  });
  it.each([true, false, { type: 'boolean' }])('keeps explicit root type authoritative for additionalProperties=%j', additionalProperties => {
    const schema = { type: 'object', additionalProperties };
    const normalized = normalize({ schema, requiredParams: ['additionalProperties'] });
    expect(normalized.inputSchema).toEqual({ ...schema, properties: { additionalProperties: { type: 'string' } }, required: ['additionalProperties'] });
    expect(() => validateTrialInput({ additionalProperties: 'declared' }, normalized.inputSchema)).not.toThrow();
    expect(() => validateTrialInput({ additionalProperties: false }, normalized.inputSchema)).toThrow(/参数校验失败/);
  });
  it.each([true, false])('keeps untyped additionalProperties=%j as a root keyword rather than a business-field descriptor', additionalProperties => {
    const normalized = normalize({ schema: { additionalProperties }, requiredParams: ['additionalProperties'] });
    expect(normalized.inputSchema).toEqual({ type: 'object', additionalProperties, properties: { additionalProperties: { type: 'string' } }, required: ['additionalProperties'] });
    expect(() => validateTrialInput({ additionalProperties: 'declared' }, normalized.inputSchema)).not.toThrow();
    expect(() => validateTrialInput({ additionalProperties: false }, normalized.inputSchema)).toThrow(/参数校验失败/);
  });
  it('keeps explicit root conditionals authoritative when requiredParams also names an ambiguous keyword', () => {
    const schema = { additionalProperties: { type: 'boolean' }, if: { required: ['trigger'] }, then: { required: ['billingAddress'] } };
    const normalized = normalize({ schema, requiredParams: ['additionalProperties'] });
    expect(normalized.inputSchema).toEqual({ ...schema, type: 'object', properties: { additionalProperties: { type: 'string' } }, required: ['additionalProperties'] });
    expect(() => validateTrialInput({ additionalProperties: 'declared', trigger: false }, normalized.inputSchema)).toThrow(/参数校验失败/);
    expect(() => validateTrialInput({ additionalProperties: 'declared', trigger: false, billingAddress: false }, normalized.inputSchema)).not.toThrow();
  });
  it('keeps explicit root properties authoritative even without a root type', () => {
    const schema = { properties: { additionalProperties: { type: 'boolean' } }, additionalProperties: false };
    const normalized = normalize({ schema, requiredParams: ['additionalProperties'] });
    expect(normalized.inputSchema).toEqual({ ...schema, type: 'object', required: ['additionalProperties'] });
    expect(() => validateTrialInput({ additionalProperties: false }, normalized.inputSchema)).not.toThrow();
    expect(() => validateTrialInput({ additionalProperties: 'false' }, normalized.inputSchema)).toThrow(/参数校验失败/);
    expect(() => validateTrialInput({ additionalProperties: false, unexpected: true }, normalized.inputSchema)).toThrow(/参数校验失败/);
  });
  it.each([
    { schema: [] }, { schema: 'text' }, { schema: { query: 'string' } }, { schema: { properties: [] } },
    { schema: { properties: { query: 3 } } }, { requiredParams: 'query' }, { requiredParams: [3] }, { params: [] },
  ])('rejects malformed input definitions: %j', definition => {
    expect(() => normalize(definition)).toThrow(/参数定义/);
  });
});
