import {
  parseSkillParameterSchema,
  skillParameterService,
  validateSkillParameters,
  type SkillParameterField,
} from '@/services/botWorkshop/skillParameterService';
import { useEffect, useState } from 'react';
import { toast } from 'sonner';
export function useSkillParameters(botId: string, skillId: string, content: string, ownerId?: string) {
  const [schema, setSchema] = useState<SkillParameterField[]>([]);
  const [values, setValues] = useState<Record<string, unknown>>({});
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    let active = true;
    setLoading(true);
    setError('');
    setSchema([]);
    setValues({});
    void (async () => {
      const fields = parseSkillParameterSchema(content);
      const saved = fields.length ? await skillParameterService.get(botId, skillId, ownerId) : {};
      if (active) {
        setSchema(fields);
        setValues({ ...Object.fromEntries(fields.map((field) => [field.name, field.default ?? ''])), ...saved });
      }
    })()
      .catch((e) => {
        if (active) setError(e instanceof Error ? e.message : '参数读取失败');
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [botId, skillId, ownerId, content, revision]);
  const save = async () => {
    if (loading || saving || error) return;
    setSaving(true);
    try {
      validateSkillParameters(schema, values);
      await skillParameterService.save(botId, skillId, values, ownerId);
      setRevision((n) => n + 1);
      toast.success('Skill 参数已保存');
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '参数保存失败');
    } finally {
      setSaving(false);
    }
  };
  return {
    schema,
    values,
    error,
    loading,
    saving,
    save,
    retry: () => setRevision((n) => n + 1),
    change: (name: string, value: unknown) => setValues((current) => ({ ...current, [name]: value })),
  };
}
