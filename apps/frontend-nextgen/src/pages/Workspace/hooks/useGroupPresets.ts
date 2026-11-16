import type { GroupContextPreset, GroupContextView } from '@/services/workspace/groupContextService';
import { addPreset, deletePreset, listPresets, updatePreset } from '@/services/workspace/groupContextService';
import { useEffect, useRef, useState } from 'react';

type PresetList = Awaited<ReturnType<typeof listPresets>>;
type Draft = GroupContextPreset & { originalName?: string };

export function useGroupPresets(view: GroupContextView) {
  const { groupId, sessionId, view_bot_id: botId } = view;
  const [list, setList] = useState<PresetList>();
  const [draft, setDraft] = useState<Draft>();
  const [error, setError] = useState('');
  const [attempt, setAttempt] = useState(0);
  const [busy, setBusy] = useState(false);
  const writing = useRef(false);

  useEffect(() => {
    const controller = new AbortController();
    listPresets({ groupId, sessionId, view_bot_id: botId }, controller.signal).then(
      (result) => {
        if (controller.signal.aborted) return;
        if (Array.isArray(result.presets)) setList(result);
        else setError('群规则加载失败');
      },
      () => {
        if (!controller.signal.aborted) setError('群规则加载失败');
      },
    );
    return () => controller.abort();
  }, [groupId, sessionId, botId, attempt]);

  function reload() {
    setList(undefined);
    setError('');
    setAttempt((previous) => previous + 1);
  }

  async function write(action: () => Promise<unknown>) {
    if (writing.current) return;
    writing.current = true;
    setBusy(true);
    setError('');
    try {
      await action();
      setDraft(undefined);
      reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : '群规则保存失败');
    } finally {
      writing.current = false;
      setBusy(false);
    }
  }

  const canWrite = !botId && list?.can_manage === true;

  function savePreset(preset: GroupContextPreset, originalName?: string) {
    return write(() => originalName === undefined
      ? addPreset(groupId, preset)
      : updatePreset(groupId, originalName, preset));
  }

  function removePreset(name: string) {
    return write(() => deletePreset(groupId, name));
  }

  function cancelDraft() {
    setDraft(undefined);
    setError('');
  }

  return { list, draft, setDraft, error, busy, canWrite, reload, savePreset, removePreset, cancelDraft };
}
