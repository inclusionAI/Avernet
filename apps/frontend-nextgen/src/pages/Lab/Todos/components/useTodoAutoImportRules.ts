import type { CreateTodoAutoImportRuleInput, TodoAutoImportRule } from '@/domain/todos/models';
import {
  createTodoAutoImportRule,
  loadTodoAutoImportRules,
  updateTodoAutoImportRule,
} from '@/services/todos/todoService';
import { useCallback, useEffect, useRef, useState } from 'react';

interface UseTodoAutoImportRulesOptions {
  active: boolean;
  spaceId?: number;
}

export function useTodoAutoImportRules({ active, spaceId }: UseTodoAutoImportRulesOptions) {
  const [rules, setRules] = useState<TodoAutoImportRule[]>([]);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [updatingRuleId, setUpdatingRuleId] = useState('');
  const [error, setError] = useState('');
  const requestGeneration = useRef(0);

  const refresh = useCallback(async () => {
    if (!spaceId) return;
    const generation = ++requestGeneration.current;
    setLoading(true);
    setError('');
    try {
      const result = await loadTodoAutoImportRules(spaceId);
      if (generation !== requestGeneration.current) return;
      setRules(result);
    } catch {
      if (generation !== requestGeneration.current) return;
      setRules([]);
      setError('自动导入规则加载失败，请重试。');
    } finally {
      if (generation === requestGeneration.current) setLoading(false);
    }
  }, [spaceId]);

  useEffect(() => {
    requestGeneration.current += 1;
    setLoading(false);
    setSaving(false);
    setUpdatingRuleId('');
    setError('');
    if (active && spaceId) void refresh();
  }, [active, refresh, spaceId]);

  const createRule = async (input: CreateTodoAutoImportRuleInput) => {
    setSaving(true);
    setError('');
    try {
      const created = await createTodoAutoImportRule(input);
      setRules((current) => [created, ...current.filter((rule) => rule.ruleId !== created.ruleId)]);
      return true;
    } catch {
      setError('自动导入规则保存失败，请重试。');
      return false;
    } finally {
      setSaving(false);
    }
  };

  const setRuleEnabled = async (rule: TodoAutoImportRule, enabled: boolean) => {
    setUpdatingRuleId(rule.ruleId);
    setError('');
    try {
      const updated = await updateTodoAutoImportRule(rule.ruleId, {
        status: enabled ? 'enabled' : 'disabled',
      });
      setRules((current) => current.map((item) => (item.ruleId === updated.ruleId ? updated : item)));
    } catch {
      setError('自动导入规则状态更新失败，请重试。');
    } finally {
      setUpdatingRuleId('');
    }
  };

  return {
    rules,
    loading,
    saving,
    updatingRuleId,
    error,
    refresh,
    createRule,
    setRuleEnabled,
  };
}
