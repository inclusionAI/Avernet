/** @jest-environment node */
import * as templateController from '@/services/backendApi/collaboration/collaborationTemplateController';
import { collaborationTemplateService } from '@/services/workspace/collaborationTemplateService';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';

jest.mock('@/services/backendApi/collaboration/collaborationTemplateController');

const tc = templateController as unknown as Record<string, jest.Mock<any>>;

beforeEach(() => {
  jest.resetAllMocks();
});

describe('collaborationTemplateService', () => {
  it('list unwraps envelope data and applies defaults', async () => {
    tc.listCollaborationTemplates.mockResolvedValue({
      code: 20000,
      data: {
        templates: [
          { id: 't1', name: '模板一', priority: 2, tags: ['qa'] },
          { id: 't2', name: '模板二', priority: 1, tags: [] },
        ],
        tag_labels: { qa: { 'zh-CN': '问答' } },
        default_language: 'zh-CN',
        supported_languages: ['zh-CN'],
      },
    });

    const res = await collaborationTemplateService.list();

    expect(res.ok).toBe(true);
    if (!res.ok) return;
    expect(res.data.templates).toHaveLength(2);
    expect(res.data.tagLabels.qa['zh-CN']).toBe('问答');
    expect(res.data.defaultLanguage).toBe('zh-CN');
  });

  it('list returns fallback defaults when data missing', async () => {
    tc.listCollaborationTemplates.mockResolvedValue({ code: 20000 });

    const res = await collaborationTemplateService.list();

    expect(res.ok && res.data.templates).toEqual([]);
    expect(res.ok && res.data.defaultLanguage).toBe('zh-CN');
  });

  it('list returns domain error on throw', async () => {
    tc.listCollaborationTemplates.mockRejectedValue(new Error('boom'));

    const res = await collaborationTemplateService.list();

    expect(res.ok).toBe(false);
    if (res.ok) return;
    expect(res.error.code).toBe('TEMPLATES_LOAD_FAILED');
  });

  it('getYaml unwraps string from envelope data', async () => {
    tc.getCollaborationTemplateYaml.mockResolvedValue({ code: 20000, data: 'name: yaml' });

    const res = await collaborationTemplateService.getYaml('t1', 'zh-CN');

    expect(tc.getCollaborationTemplateYaml).toHaveBeenCalledWith({ template_id: 't1', lang: 'zh-CN' });
    expect(res.ok && res.data).toBe('name: yaml');
  });

  it('getYaml handles raw string (text/yaml content-type)', async () => {
    tc.getCollaborationTemplateYaml.mockResolvedValue('name: raw-yaml\nparticipants:\n  - alpha');

    const res = await collaborationTemplateService.getYaml('t1');

    expect(res.ok && res.data).toBe('name: raw-yaml\nparticipants:\n  - alpha');
  });

  it('getYaml coerces non-string data to string', async () => {
    tc.getCollaborationTemplateYaml.mockResolvedValue({ code: 20000, data: 123 });

    const res = await collaborationTemplateService.getYaml('t1');

    expect(res.ok && res.data).toBe('123');
  });

  it('getYaml returns domain error on throw', async () => {
    tc.getCollaborationTemplateYaml.mockRejectedValue(new Error('boom'));

    const res = await collaborationTemplateService.getYaml('t1');

    expect(res.ok).toBe(false);
    if (res.ok) return;
    expect(res.error.code).toBe('TEMPLATE_YAML_LOAD_FAILED');
  });
});
