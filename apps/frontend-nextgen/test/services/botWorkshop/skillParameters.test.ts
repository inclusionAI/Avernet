import { parseSkillParameterSchema, validateSkillParameters } from '@/services/botWorkshop/skillParameterService';
test('parses legacy config and preserves zero/false required values', () => {
  const schema = parseSkillParameterSchema(
    '---\nconfig:\n  - name: count\n    type: number\n    required: true\n  - name: enabled\n    type: boolean\n    required: true\n---\n',
  );
  expect(schema).toHaveLength(2);
  expect(() => validateSkillParameters(schema, { count: 0, enabled: false })).not.toThrow();
  expect(() => validateSkillParameters(schema, {})).toThrow('必填');
});
test('does not treat a malformed schema as an empty successful definition', () => {
  expect(() => parseSkillParameterSchema('---\nparameters: nope\n---\n')).toThrow();
});
