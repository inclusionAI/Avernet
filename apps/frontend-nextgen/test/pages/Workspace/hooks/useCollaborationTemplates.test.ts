/** @jest-environment jsdom */
import { useCollaborationTemplates } from '@/pages/Workspace/hooks/useCollaborationTemplates';
import { collaborationTemplateService } from '@/services/workspace/collaborationTemplateService';
import { beforeEach, expect, it, jest } from '@jest/globals';
import { act, renderHook, waitFor } from '@testing-library/react';

jest.mock('@/services/workspace/collaborationTemplateService');

const ts = collaborationTemplateService as unknown as Record<string, jest.Mock<any>>;

function tmpl(id: string, priority = 0, langs: string[] = ['zh-CN']) {
  return { id, name: id, description: '', participants: {}, tags: [], priority, available_languages: langs };
}

beforeEach(() => {
  jest.resetAllMocks();
});

it('defaults to template mode and does not fetch when disabled', () => {
  const onYaml = jest.fn();
  const { result } = renderHook(() => useCollaborationTemplates(false, onYaml));
  expect(result.current.mode).toBe('template');
  expect(ts.list).not.toHaveBeenCalled();
});

it('auto-loads templates and first yaml when enabled, echoing into editor', async () => {
  ts.list.mockResolvedValue({
    ok: true,
    data: { templates: [tmpl('b', 2), tmpl('a', 1)], tagLabels: {}, defaultLanguage: 'zh-CN' },
  });
  ts.getYaml.mockResolvedValue({ ok: true, data: 'yaml-a' });
  const onYaml = jest.fn();
  const { result } = renderHook(() => useCollaborationTemplates(true, onYaml));

  await waitFor(() => expect(result.current.templates).toHaveLength(2));
  await waitFor(() => expect(result.current.selectedTemplateId).toBe('a'));
  await waitFor(() => expect(onYaml).toHaveBeenCalledWith('yaml-a'));
});

it('resets state when disabled', async () => {
  ts.list.mockResolvedValue({
    ok: true,
    data: { templates: [tmpl('a', 1)], tagLabels: {}, defaultLanguage: 'zh-CN' },
  });
  ts.getYaml.mockResolvedValue({ ok: true, data: 'yaml-a' });
  const onYaml = jest.fn();
  const { result, rerender } = renderHook(({ enabled }) => useCollaborationTemplates(enabled, onYaml), {
    initialProps: { enabled: true },
  });

  await waitFor(() => expect(result.current.templates).toHaveLength(1));

  rerender({ enabled: false });

  expect(result.current.mode).toBe('template');
  expect(result.current.templates).toHaveLength(0);
});

it('switching to free mode clears yaml', async () => {
  ts.list.mockResolvedValue({
    ok: true,
    data: { templates: [tmpl('a', 1)], tagLabels: {}, defaultLanguage: 'zh-CN' },
  });
  ts.getYaml.mockResolvedValue({ ok: true, data: 'yaml-a' });
  const onYaml = jest.fn();
  const { result } = renderHook(() => useCollaborationTemplates(true, onYaml));

  await waitFor(() => expect(onYaml).toHaveBeenCalledWith('yaml-a'));

  await act(async () => {
    result.current.setMode('free');
  });

  expect(onYaml).toHaveBeenCalledWith('');
});

it('selectTemplate loads yaml for the chosen template', async () => {
  ts.list.mockResolvedValue({
    ok: true,
    data: { templates: [tmpl('a', 0), tmpl('b', 1)], tagLabels: {}, defaultLanguage: 'zh-CN' },
  });
  ts.getYaml.mockResolvedValueOnce({ ok: true, data: 'yaml-a' });
  ts.getYaml.mockResolvedValueOnce({ ok: true, data: 'yaml-b' });
  const onYaml = jest.fn();
  const { result } = renderHook(() => useCollaborationTemplates(true, onYaml));

  await waitFor(() => expect(onYaml).toHaveBeenCalledWith('yaml-a'));

  await act(async () => {
    result.current.selectTemplate(tmpl('b', 0));
  });
  await waitFor(() => expect(onYaml).toHaveBeenCalledWith('yaml-b'));
  expect(result.current.selectedTemplateId).toBe('b');
});

it('tagLabel falls back to raw tag before list is loaded', () => {
  const onYaml = jest.fn();
  const { result } = renderHook(() => useCollaborationTemplates(false, onYaml));
  expect(result.current.tagLabel('unknown')).toBe('unknown');
});
