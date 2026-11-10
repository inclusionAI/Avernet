import { validateDynamicTemplateFields } from '@/components/BotWorkshop/CreateBotModal/agentCoding/DynamicTemplateFieldLogic';
import type { BotTemplateField } from '@/services/botWorkshop/agentCodingTemplateService';

const fields: BotTemplateField[] = [
  { field_key: 'backend_repo', field_name: '后端代码仓库', field_type: 'antcode' },
  { field_key: 'frontend_repo', field_name: '前端代码仓库', field_type: 'antcode' },
];

describe('validateDynamicTemplateFields', () => {
  test('不同代码仓库字段之间的相同仓库也会阻断', () => {
    const error = validateDynamicTemplateFields(fields, {
      backend_repo: [{ repo_url: 'https://example.com/repo-a.git' }],
      frontend_repo: [{ repo_url: 'https://example.com/repo-a.git' }],
    });

    expect(error).toBe(
      '代码仓库字段存在重复的仓库地址：https://example.com/repo-a.git，同一 Bot 内不可配置重复仓库',
    );
  });

  test('不同代码仓库字段填写不同仓库时不误报', () => {
    const error = validateDynamicTemplateFields(fields, {
      backend_repo: [{ repo_url: 'https://example.com/repo-a.git' }],
      frontend_repo: [{ repo_url: 'https://example.com/repo-b.git' }],
    });

    expect(error).toBeNull();
  });
});
