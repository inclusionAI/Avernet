import {
  collaborationTemplateService,
  type CollaborationTemplate,
} from '@/services/workspace/collaborationTemplateService';
import { useCallback, useEffect, useRef, useState } from 'react';

export type TemplateMode = 'free' | 'template';

export interface UseCollaborationTemplatesResult {
  mode: TemplateMode;
  templates: CollaborationTemplate[];
  selectedTemplateId: string | null;
  selectedTemplate: CollaborationTemplate | null;
  loadingTemplates: boolean;
  loadingYaml: boolean;
  tagLabel: (tag: string) => string;
  setMode: (mode: TemplateMode) => void;
  selectTemplate: (template: CollaborationTemplate) => void;
  reset: () => void;
}

const byPriority = (list: CollaborationTemplate[]) => [...list].sort((a, b) => (a.priority ?? 0) - (b.priority ?? 0));

/**
 * 自定义协作模板 Hook：管理「自由编辑 / 模板」模式切换、模板列表加载与模板 YAML 拉取。
 *
 * 与 open-claw 对齐：enabled 变 true 时自动拉取模板列表并回写首个模板 YAML 到编辑器，
 * 默认停留在「模板」模式；切到「自由编辑」清空编辑器，切回「模板」重新回显选中模板。
 * YAML 拉取成功后通过 onYaml 回写调用方（Modal 持有的 definitionYaml 状态）。
 */
export function useCollaborationTemplates(
  enabled: boolean,
  onYaml: (yaml: string) => void,
): UseCollaborationTemplatesResult {
  const [mode, setModeState] = useState<TemplateMode>('template');
  const [templates, setTemplates] = useState<CollaborationTemplate[]>([]);
  const [tagLabels, setTagLabels] = useState<Record<string, Record<string, string>>>({});
  const [defaultLanguage, setDefaultLanguage] = useState('zh-CN');
  const [selectedTemplateId, setSelectedTemplateId] = useState<string | null>(null);
  const [loadingTemplates, setLoadingTemplates] = useState(false);
  const [loadingYaml, setLoadingYaml] = useState(false);
  const loadedRef = useRef(false);
  const loadingRef = useRef(false);
  const requestIdRef = useRef(0);
  const onYamlRef = useRef(onYaml);
  onYamlRef.current = onYaml;

  const sortedTemplates = byPriority(templates);
  const selectedTemplate = sortedTemplates.find((t) => t.id === selectedTemplateId) ?? null;

  const tagLabel = useCallback(
    (tag: string) => {
      const labels = tagLabels[tag];
      return labels?.[defaultLanguage] || labels?.[Object.keys(labels)[0]] || tag;
    },
    [tagLabels, defaultLanguage],
  );

  const resolveLang = useCallback(
    (template: CollaborationTemplate) =>
      template.available_languages?.includes(defaultLanguage)
        ? defaultLanguage
        : template.available_languages?.[0] || defaultLanguage,
    [defaultLanguage],
  );

  const loadYaml = useCallback(
    async (template: CollaborationTemplate, lang?: string) => {
      const requestId = ++requestIdRef.current;
      setLoadingYaml(true);
      const res = await collaborationTemplateService.getYaml(template.id, lang ?? resolveLang(template));
      if (requestId !== requestIdRef.current) return;
      setLoadingYaml(false);
      if (res.ok) onYamlRef.current(res.data);
    },
    [resolveLang],
  );

  const loadTemplates = useCallback(async () => {
    if (loadedRef.current || loadingRef.current) return;
    loadingRef.current = true;
    const requestId = ++requestIdRef.current;
    setLoadingTemplates(true);
    const res = await collaborationTemplateService.list();
    if (requestId !== requestIdRef.current) {
      loadingRef.current = false;
      return;
    }
    if (res.ok) {
      const sorted = byPriority(res.data.templates);
      setTemplates(sorted);
      setTagLabels(res.data.tagLabels);
      setDefaultLanguage(res.data.defaultLanguage);
      loadedRef.current = true;
      if (sorted.length > 0) {
        setSelectedTemplateId(sorted[0].id);
        await loadYaml(sorted[0], res.data.defaultLanguage);
      }
    }
    setLoadingTemplates(false);
    loadingRef.current = false;
  }, [loadYaml]);

  const selectTemplate = useCallback(
    (template: CollaborationTemplate) => {
      if (template.id === selectedTemplateId) return;
      setSelectedTemplateId(template.id);
      void loadYaml(template);
    },
    [selectedTemplateId, loadYaml],
  );

  const setMode = useCallback(
    (next: TemplateMode) => {
      if (next === mode) return;
      setModeState(next);
      if (next === 'free') {
        onYamlRef.current('');
        return;
      }
      const target =
        (selectedTemplateId && sortedTemplates.find((t) => t.id === selectedTemplateId)) || sortedTemplates[0];
      if (target) {
        if (target.id !== selectedTemplateId) setSelectedTemplateId(target.id);
        void loadYaml(target);
      } else if (!loadedRef.current) {
        void loadTemplates();
      }
    },
    [mode, selectedTemplateId, sortedTemplates, loadYaml, loadTemplates],
  );

  const reset = useCallback(() => {
    setModeState('template');
    setSelectedTemplateId(null);
    setTemplates([]);
    setTagLabels({});
    setDefaultLanguage('zh-CN');
    setLoadingTemplates(false);
    setLoadingYaml(false);
    loadedRef.current = false;
    loadingRef.current = false;
  }, []);

  useEffect(() => {
    if (!enabled) {
      reset();
      return;
    }
    if (!loadedRef.current) void loadTemplates();
  }, [enabled, loadTemplates, reset]);

  return {
    mode,
    templates: sortedTemplates,
    selectedTemplateId,
    selectedTemplate,
    loadingTemplates,
    loadingYaml,
    tagLabel,
    setMode,
    selectTemplate,
    reset,
  };
}
