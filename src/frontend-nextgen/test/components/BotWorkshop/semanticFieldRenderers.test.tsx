import {
  getSemanticTemplateFieldInitialValue,
  isSemanticTemplateField,
  sanitizeSemanticTemplateFieldValue,
} from '@/components/BotWorkshop/CreateBotModal/agentCoding/fields/semanticFieldRenderers';
import { describe, expect, it } from '@jest/globals';

const architectNameField = {
  field_key: 'architect_name',
  field_name: '架构域名称',
  field_type: 'input',
  required: true,
};

describe('semantic architect name field', () => {
  it('将 architect_name 识别为架构域选择字段并保留名称值', () => {
    expect(isSemanticTemplateField(architectNameField)).toBe(true);
    expect(
      getSemanticTemplateFieldInitialValue(architectNameField, {
        architect_name: '支付架构域',
      }),
    ).toEqual({ handled: true, value: '支付架构域' });
    expect(sanitizeSemanticTemplateFieldValue(architectNameField, '  支付架构域  ')).toEqual({
      handled: true,
      value: '支付架构域',
    });
  });
});
